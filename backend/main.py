"""FastAPI backend for CogniSync."""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uuid
import json
import asyncio
import os
import tempfile

from . import storage
from .config import OPENROUTER_API_KEY, COUNCIL_MODELS, CHAIRMAN_MODEL, FREE_TESTING_MODE
from .council import run_full_council, generate_conversation_title, stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final, calculate_aggregate_rankings
from .query import run_query, resolve_selected
from .ddg_search import DuckDuckGoSearchService, format_search_context
from .integrations import GitHubClient, NotionClient
from .upload_validation import (
    validate_extension,
    validate_size,
    validate_mime_type,
    validate_attachment_count,
    MAX_ATTACHMENT_SIZE,
)
from .document_processing import extract_attachment, ExtractionResult

app = FastAPI(title="CogniSync API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reject_encoded_conversation_ids(request, call_next):
    """Encoded separators/dots would be normalized away before routing.

    Reject them explicitly so malformed conversation URLs get a clear 400
    instead of an ambiguous 404 from path normalization.
    """
    raw_path = request.scope.get("raw_path", b"").decode("latin-1")
    if raw_path.startswith("/api/conversations/") and "%" in raw_path:
        return JSONResponse(status_code=400, content={"detail": "Invalid conversation id"})
    return await call_next(request)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


def _require_conversation_id(conversation_id: str) -> None:
    """Reject ids that are not plain UUIDs at the API boundary.

    The frontend generates canonical UUIDs; anything else (path traversal,
    encoded separators, empty strings, urls) gets a 400 before it reaches
    storage. Storage still validates independently.
    """
    try:
        parsed = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid conversation id") from None
    if str(parsed) != conversation_id:
        raise HTTPException(status_code=400, detail="Invalid conversation id")


class AttachmentMeta(BaseModel):
    """Metadata for an uploaded attachment, sent from the frontend."""
    original_filename: str
    file_size: int
    media_type: str


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation.

    Attributes:
        content: The user's message text.
        web_search: Whether to enable live DuckDuckGo search grounding.
            Defaults to False. When True, search results are retrieved
            server-side and shared with the council as grounding evidence.
        attachments: Optional list of attachment metadata from a prior upload.
    """
    content: str
    web_search: bool = False
    attachments: List[AttachmentMeta] = []
    models: Optional[List[str]] = None


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    updated_at: Optional[str] = None
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint with provider configuration status.

    Never exposes API keys or secrets. Only reports whether a provider is
    configured and which models are registered.
    """
    return {
        "status": "ok",
        "service": "CogniSync API",
        "providers": {
            "openrouter": {
                "configured": bool(OPENROUTER_API_KEY),
            },
        },
        "free_testing": FREE_TESTING_MODE,
        "council_models": COUNCIL_MODELS,
        "chairman_model": CHAIRMAN_MODEL,
    }


class QueryRequest(BaseModel):
    """Request body for POST /api/query."""
    question: str
    models: List[str] = []
    mode: str = "balanced"
    web_search: bool = False


@app.post("/api/query")
async def query_council(request: QueryRequest):
    """Run the full council pipeline for a single question.

    Returns per-model responses with timing/token counts, the chairman's
    synthesized answer, the derived discussion timeline, and the search status.
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty")
    return await run_query(request.question, request.models, request.mode, request.web_search)


@app.get("/api/models")
async def models_catalog():
    """OpenRouter free-model catalog for the UI.

    Authenticated live-check data comes only from /api/tools/test or actual
    usage — this endpoint reports catalog availability, not connectivity.
    Results are cached server-side (TTL configured in config.py); use
    POST /api/models/refresh to force a re-fetch.
    """
    from .model_catalog import get_catalog

    models, fallback = await get_catalog()
    return {
        "configured": bool(OPENROUTER_API_KEY),
        "free_testing": FREE_TESTING_MODE,
        "fallback": fallback,
        "models": models,
        "chairman_model": CHAIRMAN_MODEL,
    }


@app.post("/api/models/refresh")
async def refresh_models():
    """Force a catalog re-fetch (explicit refresh action from the UI)."""
    from .model_catalog import get_catalog

    models, fallback = await get_catalog(force=True)
    return {
        "configured": bool(OPENROUTER_API_KEY),
        "free_testing": FREE_TESTING_MODE,
        "fallback": fallback,
        "models": models,
    }


class ToolTestRequest(BaseModel):
    """Request body for POST /api/tools/test."""
    name: str


async def _test_web_search() -> Dict[str, Any]:
    """Live DuckDuckGo round-trip. No API key is involved."""
    service = DuckDuckGoSearchService()
    response = await service.search("CogniSync connection test")
    if response.status == "success":
        return {"success": True, "detail": "DuckDuckGo search round-trip succeeded.",
                "result_count": len(response.results), "duration_ms": response.total_duration_ms}
    return {"success": False, "status": response.status,
            "error": response.error_message or f"Search returned status {response.status}."}


@app.post("/api/tools/test")
async def tools_test(request: ToolTestRequest):
    """Live connection test for one integration.

    Frontend actions trigger this explicitly — it performs a real,
    minimal authenticated request and never exposes credentials.
    """
    name = (request.name or "").strip().lower()
    if name in ("duckduckgo", "duckduckgo_search", "ddg", "web_search", "websearch"):
        result = await _test_web_search()
    elif name == "github":
        result = await GitHubClient().test_connection()
        return {"name": "github", "success": result.success, **({k: v for k, v in result.to_dict().items() if k in ("data", "error", "duration_ms")})}
    elif name == "notion":
        result = await NotionClient().test_connection()
        return {"name": "notion", "success": result.success, **({k: v for k, v in result.to_dict().items() if k in ("data", "error", "duration_ms")})}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown integration '{name}'. Known: web_search, github, notion.")
    return {"name": name, **result}


class GitHubSearchRequest(BaseModel):
    query: str
    per_page: int = 5


class GitHubReadRequest(BaseModel):
    owner: str
    repo: str
    path: str
    ref: str = ""


class GitHubIssuesRequest(BaseModel):
    owner: str
    repo: str
    state: str = "open"
    per_page: int = 10


class NotionSearchRequest(BaseModel):
    query: str


class NotionPageRequest(BaseModel):
    page_id: str


def _tool_payload(result) -> Dict[str, Any]:
    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


@app.post("/api/tools/github/search")
async def github_search(request: GitHubSearchRequest):
    """Search GitHub repositories (read-only official REST API)."""
    return _tool_payload(await GitHubClient().search_repositories(request.query, request.per_page))


@app.post("/api/tools/github/file")
async def github_file(request: GitHubReadRequest):
    """Read a single repository file (read-only)."""
    return _tool_payload(await GitHubClient().get_file_contents(request.owner, request.repo, request.path, request.ref))


@app.post("/api/tools/github/issues")
async def github_issues(request: GitHubIssuesRequest):
    """List issues for a repository (read-only)."""
    return _tool_payload(await GitHubClient().list_issues(request.owner, request.repo, request.state, request.per_page))


@app.post("/api/tools/notion/search")
async def notion_search(request: NotionSearchRequest):
    """Search Notion pages shared with the integration (read-only)."""
    return _tool_payload(await NotionClient().search_pages(request.query))


@app.post("/api/tools/notion/page")
async def notion_page(request: NotionPageRequest):
    """Retrieve a Notion page's text content (read-only)."""
    return _tool_payload(await NotionClient().get_page_content(request.page_id))


@app.get("/api/tools/status")
async def tools_status():
    """Integration configuration status.

    Reports only server-side configuration state — 'configured' means the
    credential environment variable exists; it is verified live only via
    POST /api/tools/test. Never exposes tokens.
    """
    return {
        "authentication": "none",
        "integrations": {
            "web_search": {
                "kind": "library_search_no_api_key",
                "configured": bool(DuckDuckGoSearchService().configured),
                "label": "DuckDuckGo Search",
                "note": "Library-based DuckDuckGo search (no API key required). Queries run server-side; successful results ground council requests when web search is on.",
                "actions": ["test", "enable"], "perms": "read",
            },
            "github": {
                "kind": "rest_official_api",
                "configured": bool(os.getenv("GITHUB_TOKEN")),
                "label": "GitHub",
                "note": "Direct official GitHub REST API. Set GITHUB_TOKEN server-side for authenticated access; otherwise public repositories are used at lower rate limits. Read-only.",
                "actions": ["test", "search", "read_file", "list_issues"], "perms": "read",
            },
            "notion": {
                "kind": "rest_official_api",
                "configured": bool(os.getenv("NOTION_TOKEN")),
                "label": "Notion",
                "note": "Direct official Notion API. Requires NOTION_TOKEN (internal integration secret) set server-side and pages shared with the integration. Read-only.",
                "actions": ["test", "search", "read_page"], "perms": "read",
            },
        },
    }


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    _require_conversation_id(conversation_id)
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
):
    """Send a message and run the 3-stage council process.

    Accepts JSON body with ``content``, ``web_search``, and optional
    ``attachments`` metadata.     This endpoint is for text-only or text-plus-previously-uploaded-attachments messages.
    """
    _require_conversation_id(conversation_id)
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Run the full council (attachments metadata only) and return the result.
    result = await _process_and_run_council(conversation_id, request)
    return result


async def _resolve_request_models(chosen: Optional[List[str]] = None) -> Dict[str, Any]:
    """Resolve the council model ids for a run.

    Without user selection: the catalog-resolved default free set. With
    selection: dedupe and validate every id against the catalog — in free
    testing mode a non-free or unknown id is rejected with a 400 rather
    than silently billed.
    """
    from .config import COUNCIL_MODEL_COUNT
    from .model_catalog import resolve_council_models, get_catalog

    if not chosen:
        return await resolve_council_models(COUNCIL_MODEL_COUNT)

    ids = list(dict.fromkeys(m for m in chosen if isinstance(m, str) and m.strip()))
    catalog_models, _fallback = await get_catalog()
    free_ids = {m["id"] for m in catalog_models if m.get("free", True)}
    names = {m["id"]: m["name"] for m in catalog_models}

    unknown = [m for m in ids if m not in free_ids]
    if FREE_TESTING_MODE and unknown:
        raise _PaidModelError(unknown)
    if not ids:
        raise _PaidModelError(["all selected ids were invalid"])

    return {
        "ids": ids,
        "names": names,
        "fallback": False,
        "deficient": len(ids) < COUNCIL_MODEL_COUNT,
        "note": f"{len(ids)} of {COUNCIL_MODEL_COUNT} requested council models selected." if len(ids) < COUNCIL_MODEL_COUNT else "",
        "requested": COUNCIL_MODEL_COUNT,
        "alternatives": _build_alternatives(ids, catalog_models),
    }


def _build_alternatives(ids: List[str], catalog_models: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """For each council slot, 2 verified-free substitutes from the catalog.

    Substitutes avoid the slots' own ids, prefer DIFFERENT provider prefixes
    (id before '/') and families, so one exhausted provider/network does not
    sink every slot. Catalog entries are pricing-verified free — never paid.
    """
    chosen = set(ids)
    pool = [m for m in catalog_models if m["id"] not in chosen]
    chosen_prefixes = {i.split("/")[0] for i in ids}
    alternatives: Dict[str, List[str]] = {}
    for slot in ids:
        slot_family = next((m.get("family") for m in catalog_models if m["id"] == slot), None)
        ranked = sorted(
            pool,
            key=lambda m: (
                m["id"].split("/")[0] in chosen_prefixes,  # prefer other providers
                m.get("family") == slot_family,
                m["id"],
            ),
        )
        picks = []
        for m in ranked:
            if m["id"] not in picks:
                picks.append(m["id"])
            if len(picks) >= 2:
                break
        alternatives[slot] = picks
    return alternatives


class _PaidModelError(Exception):
    def __init__(self, ids):
        self.ids = ids
        super().__init__("paid model ids not eligible in free-testing mode")



def _history_context(conversation: Dict[str, Any], max_turns: int = 6) -> str:
    """Compact prior-turn context for follow-ups in the same session.

    Uses stored plain content (fallback to JSON-decoded content) plus each
    assistant synthesis, bounded to recent turns.
    """
    lines = []
    for msg in (conversation.get("messages") or [])[-max_turns:]:
        if msg.get("role") == "user":
            text = msg.get("content") or ""
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and parsed.get("content"):
                    text = parsed["content"]
            except (ValueError, TypeError):
                pass
            if text.strip():
                lines.append(f"User asked: {text.strip()[:500]}")
        elif msg.get("role") == "assistant":
            stage3 = msg.get("stage3") or {}
            synthesis = (stage3 or {}).get("response") or ""
            if stage3.get("success") is not False and synthesis.strip():
                lines.append(f"Council answered: {synthesis.strip()[:800]}")
    if not lines:
        return ""
    return "Previous turns in this conversation:\n" + "\n".join(lines) + "\n\n"


async def _prepare_council_run(
    conversation: Dict[str, Any],
    content: str,
    web_search: bool,
    context_parts: List[str],
    user_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist the user message and compose the effective council question.

    Shared blocking and streaming paths. The user message is saved FIRST so
    it survives any later failure, with retry dedupe in storage.
    """
    conversation_id = conversation["id"]
    user_entry = {
        "role": "user",
        "content": content,
        "web_search": web_search,
        "has_attachments": bool(context_parts),
        "attachment_count": len(context_parts),
    }
    if user_extra:
        user_entry.update(user_extra)
    storage.add_user_message(conversation_id, json.dumps(user_entry))

    history_block = _history_context(storage.get_conversation(conversation_id) or conversation)

    search_context: Optional[str] = None
    sources: Optional[List[Dict[str, Any]]] = None
    search_metadata: Dict[str, Any] = {"enabled": web_search}
    combined_parts: List[str] = list(context_parts)

    if web_search:
        service = DuckDuckGoSearchService()
        search_response = await service.search(content)
        search_metadata.update({
            "provider": "duckduckgo",
            "status": search_response.status,
            "total_duration_ms": search_response.total_duration_ms,
        })
        if search_response.results:
            search_metadata["result_count"] = len(search_response.results)
        if search_response.error_message:
            search_metadata["error_message"] = search_response.error_message
        search_context = format_search_context(search_response)
        if search_context:
            sources = [
                {"result_id": r.result_id, "title": r.title, "url": r.url, "domain": r.domain}
                for r in search_response.results
            ]
            combined_parts.append(f"--- Web Search Evidence ---\n{search_context}")
    else:
        search_response = None

    if history_block:
        combined_parts.append(history_block.rstrip())

    if combined_parts:
        combined_context = "\n\n".join(combined_parts) + "\n\n"
        effective_question = f"{combined_context}User question: {content}".strip()
    else:
        effective_question = content

    return {
        "effective_question": effective_question,
        "sources": sources,
        "search_metadata": search_metadata,
    }


def _finalize_council_result(
    conversation_id: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    stage3_result: Dict[str, Any],
    metadata: Dict[str, Any],
    search_metadata: Dict[str, Any],
    sources: Optional[List[Dict[str, Any]]],
    resolved: Dict[str, Any],
    label_to_model: Optional[Dict[str, str]] = None,
    aggregate: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Map display names, persist the assistant message, build the payload.

    Shared terminal step — identical for blocking and streaming runs.
    """
    names = resolved["names"]
    for entry in stage1_results + stage2_results:
        if entry.get("model") in names:
            entry["model_id"] = entry["model"]
            entry["model"] = names[entry["model"]]
    for entry in stage1_results:
        if entry.get("fallback_used"):
            entry["fallback_display"] = names.get(entry["fallback_used"], entry["fallback_used"])
    if stage3_result and stage3_result.get("model") in names:
        stage3_result["model_id"] = stage3_result["model"]
        stage3_result["model"] = names[stage3_result["model"]]

    metadata = dict(metadata or {})
    metadata["search"] = search_metadata
    metadata["models_fallback"] = resolved["fallback"]
    metadata["models"] = resolved["ids"]
    effective_labels = label_to_model if label_to_model is not None else metadata.get("label_to_model", {})
    metadata["label_to_model"] = effective_labels
    metadata["aggregate_rankings"] = aggregate if aggregate is not None else calculate_aggregate_rankings(stage2_results, effective_labels)

    # Single final-answer contract: the chairman's own output, never a
    # scraped discussion excerpt. Persisted with the same conversation.
    final_text = (stage3_result or {}).get("response") or ""
    final_ok = bool(stage3_result) and stage3_result.get("success") is not False and bool(final_text.strip())
    final_answer = final_text if final_ok else None
    final_answer_error = None if final_ok else ((stage3_result or {}).get("error") or "Chairman synthesis is unavailable.")
    metadata["final_answer_model_id"] = (stage3_result or {}).get("model_id") or (stage3_result or {}).get("model")
    metadata["final_answer_error"] = final_answer_error

    storage.add_assistant_message(
        conversation_id, stage1_results, stage2_results, stage3_result, metadata=metadata,
        final_answer=final_answer, final_answer_error=final_answer_error, sources=sources,
    )

    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "final_answer": final_answer,
        "final_answer_error": final_answer_error,
        "final_answer_model": (stage3_result or {}).get("model"),
        "metadata": metadata,
        "sources": sources,
    }


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _run_council_events_stream(
    conversation: Optional[Dict[str, Any]],
    content: str,
    web_search: bool,
    context_parts: List[str],
    request_models: Optional[List[str]] = None,
    user_extra: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
):
    """SSE generator: persisted user message, then progressive council events.

    When the conversation does not exist yet, it is created only AFTER the
    model selection validates — a rejected request never creates an empty
    history record.
    """
    import asyncio

    from .council import run_council_events

    conversation_id = conversation_id or (conversation or {}).get("id")
    if not conversation_id:
        yield _sse({"type": "error", "error": 400, "message": "Missing conversation id."})
        yield _sse({"type": "done"})
        return

    # Validate the model selection BEFORE creating/persisting anything so an
    # invalid request never leaves a dangling conversation or message.
    try:
        resolved = await _resolve_request_models(request_models)
    except _PaidModelError as exc:
        yield _sse({"type": "error", "error": 400, "message": (
            "Free-testing mode: these model ids are not free-eligible in the current catalog: "
            + ", ".join(exc.ids)
        ), "stage1": []})
        yield _sse({"type": "done"})
        return

    if not content.strip():
        yield _sse({"type": "error", "error": 400, "message": "Message must not be empty.", "stage1": []})
        yield _sse({"type": "done"})
        return

    if conversation is None:
        conversation = storage.create_conversation(conversation_id)

    if "alternatives" not in resolved:
        from .model_catalog import get_catalog as _get_cat2
        cat_models2, _ = await _get_cat2()
        resolved["alternatives"] = _build_alternatives(resolved["ids"], cat_models2)

    prepared = await _prepare_council_run(
        conversation, content, web_search, context_parts, user_extra
    )
    yield _sse({"type": "session", "conversation_id": conversation_id,
                "web_search": prepared["search_metadata"]})

    if resolved.get("deficient") and resolved.get("note"):
        yield _sse({"type": "note", "message": resolved["note"]})

    # Chairman must also be catalog-eligible; prefer configured chairman,
    # else the first resolved member — never a paid or delisted model.
    from .model_catalog import get_catalog as _get_catalog
    catalog_models, _fb = await _get_catalog()
    catalog_ids = {m["id"] for m in catalog_models}
    chairman = CHAIRMAN_MODEL if CHAIRMAN_MODEL in catalog_ids else (resolved["ids"][0] if resolved["ids"] else CHAIRMAN_MODEL)

    stage1_results: List[Dict[str, Any]] = []
    stage2_results: List[Dict[str, Any]] = []
    label_to_model: Dict[str, str] = {}
    aggregate: List[Dict[str, Any]] = []
    stage3_result: Optional[Dict[str, Any]] = None

    try:
        async for event in run_council_events(
            prepared["effective_question"],
            models=resolved["ids"],
            chairman_model=chairman,
            alternatives=resolved.get("alternatives"),
        ):
            etype = event["type"]
            if etype == "stage1_result":
                stage1_results.append(event)
            elif etype == "run_failed":
                stage2_results = []
                stage3_result = {
                    "model": "error",
                    "response": "All models failed to respond. Please try again.",
                    "success": False,
                    "error": event.get("error") or "All council model requests failed.",
                }
                # Map display names, persist failure honestly, emit final.
                final = _finalize_council_result(
                    conversation_id, stage1_results, stage2_results, stage3_result,
                    {}, prepared["search_metadata"], prepared["sources"], resolved,
                    label_to_model={}, aggregate=[],
                )
                yield _sse({"type": "final", "payload": final})
                yield _sse({"type": "done"})
                return
            elif etype == "stage2_result":
                stage2_results.append(event)
            elif etype == "rankings":
                label_to_model = event.get("label_to_model", {})
                aggregate = event.get("aggregate", [])
            elif etype == "stage3_result":
                stage3_result = event["stage3"]
            # strip bulky internals we don't re-emit
            out = dict(event)
            out.pop("provider", None)
            if etype == "stage1_start":
                out["names"] = resolved["names"]
            yield _sse(out)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — never leave the session unsettled
        stage3_result = {
            "model": "error",
            "response": "The council run failed unexpectedly.",
            "success": False,
            "error": str(exc)[:300],
        }
        final = _finalize_council_result(
            conversation_id, stage1_results, stage2_results, stage3_result,
            {}, prepared["search_metadata"], prepared["sources"], resolved,
            label_to_model=label_to_model, aggregate=aggregate,
        )
        yield _sse({"type": "final", "payload": final})
        yield _sse({"type": "done"})
        return

    if stage3_result is None:
        stage3_result = {
            "model": "error",
            "response": "The council run ended before synthesis.",
            "success": False,
            "error": "Council generator terminated without a synthesis event.",
        }

    final = _finalize_council_result(
        conversation_id, stage1_results, stage2_results, stage3_result,
        {}, prepared["search_metadata"], prepared["sources"], resolved,
        label_to_model=label_to_model, aggregate=aggregate,
    )
    yield _sse({"type": "final", "payload": final})
    yield _sse({"type": "done"})


async def _run_council_for_request(
    conversation: Dict[str, Any],
    content: str,
    web_search: bool,
    context_parts: List[str],
    request_models: Optional[List[str]] = None,
    user_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the council (blocking) with attachments/search/history context and persist.

    Persists the user message and (even on failure) an assistant message so
    the UI and History always settle. Council failures never raise — they
    are stored and returned.
    """
    try:
        resolved = await _resolve_request_models(request_models)
    except _PaidModelError as exc:
        raise HTTPException(status_code=400, detail=(
            "Free-testing mode: these model ids are not free-eligible in the current catalog: "
            + ", ".join(exc.ids)
        )) from exc

    prepared = await _prepare_council_run(conversation, content, web_search, context_parts, user_extra)
    search_metadata = prepared["search_metadata"]
    if "alternatives" not in resolved:
        from .model_catalog import get_catalog as _get_cat2
        cat_models2, _ = await _get_cat2()
        resolved["alternatives"] = _build_alternatives(resolved["ids"], cat_models2)
    if resolved["deficient"]:
        search_metadata["models_note"] = (
            f"Only {len(resolved['ids'])} free-eligible models are currently available "
            f"(requested {resolved['requested']})."
        )

    from .model_catalog import get_catalog as _get_catalog
    catalog_models, _fallback = await _get_catalog()
    catalog_ids = {m["id"] for m in catalog_models}
    chairman = CHAIRMAN_MODEL if CHAIRMAN_MODEL in catalog_ids else (resolved["ids"][0] if resolved["ids"] else CHAIRMAN_MODEL)

    try:
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            prepared["effective_question"],
            web_search=False,  # search already applied server-side above
            models=resolved["ids"],
            chairman_model=chairman,
            alternatives=resolved.get("alternatives"),
        )
    except Exception as exc:  # noqa: BLE001 — never leave the session unsettled
        metadata = {}
        stage1_results, stage2_results = [], []
        stage3_result = {
            "model": "error",
            "response": "The council run failed unexpectedly.",
            "success": False,
            "error": str(exc)[:300],
        }

    return _finalize_council_result(
        conversation["id"], stage1_results, stage2_results, stage3_result,
        metadata, search_metadata, prepared["sources"], resolved,
        label_to_model=metadata.get("label_to_model") or None,
        aggregate=None,
    )


async def _process_and_run_council(conversation_id: str, request: SendMessageRequest):
    """Process attachments metadata and run the council with combined context."""
    conversation = storage.get_conversation(conversation_id) or {"id": conversation_id}

    # Attachments sent as metadata from a prior upload have no bytes here.
    context_parts = [
        f"--- Attachment: {a.original_filename} ({a.media_type}) ---"
        for a in request.attachments
    ]
    return await _run_council_for_request(
        conversation=conversation,
        content=request.content,
        web_search=request.web_search,
        context_parts=context_parts,
        request_models=request.models,
    )


@app.post(
    "/api/conversations/{conversation_id}/message/events",
    response_class=StreamingResponse,
)
async def send_message_events(
    conversation_id: str,
    request: SendMessageRequest,
):
    """Send a message and stream the council run as Server-Sent Events.

    Event order: session, stage1_start, stage1_result* (per model, as
    requests complete), stage2_start, stage2_result* (per reviewer),
    rankings, stage3_start, stage3_result, final (full persisted payload,
    same shape as ``/message``), done. run_failed replaces stages 2-3 when
    every Stage 1 model fails. The user message and the assistant result
    are persisted exactly once either way.
    """
    _require_conversation_id(conversation_id)
    conversation = storage.get_conversation(conversation_id)

    return StreamingResponse(
        _run_council_events_stream(
            conversation=conversation,
            content=request.content,
            web_search=request.web_search,
            context_parts=[],
            request_models=request.models,
            conversation_id=conversation_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(
    conversation_id: str,
    # Accept multipart form data: content field and multiple files
    content: str = Form(...),
    web_search: bool = Form(False),
    files: List[UploadFile] = File(default=[]),
):
    """Send a message with optional file attachments.

    Supports multipart/form-data with:
    - ``content``: The user's message text
    - ``web_search``: Whether to enable DuckDuckGo search grounding
    - ``files``: One or more file attachments (PDF, DOCX, TXT, MD, CSV, PNG, JPEG, WebP)

    Returns the full council result JSON (same shape as ``/message``).
    """
    _require_conversation_id(conversation_id)
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # --- Validate attachments ---
    validation_errors: List[str] = []

    if len(files) > 5:
        validation_errors.append("Too many attachments (maximum 5 allowed)")

    attachment_context_parts: List[str] = []
    attachment_meta_list: List[Dict[str, Any]] = []

    for uploaded_file in files:
        # Read the file content into memory (limited by FastAPI's parsing)
        file_bytes = await uploaded_file.read()
        file_size = len(file_bytes)
        filename = uploaded_file.filename or "unknown"

        # Validate extension
        ext_valid, ext_reason = validate_extension(filename)
        if not ext_valid:
            validation_errors.append(f"File '{filename}': {ext_reason}")
            continue

        # Validate size
        size_valid, size_reason = validate_size(file_size, filename)
        if not size_valid:
            validation_errors.append(f"File '{filename}': {size_reason}")
            continue

        # Validate MIME type (secondary check)
        mime_valid, mime_reason = validate_mime_type(filename, uploaded_file.content_type)
        if not mime_valid:
            validation_errors.append(f"File '{filename}': {mime_reason}")
            continue

        # Save to temporary file for extraction
        tmp_path = os.path.join(tempfile.gettempdir(), f"llm_council_{filename or 'upload'}_{uuid.uuid4().hex[:8]}")
        try:
            with open(tmp_path, "wb") as f:
                f.write(file_bytes)

            # Determine media type
            media_type = uploaded_file.content_type or "application/octet-stream"

            # Extract text using document processing module
            extraction_result = extract_attachment(
                file_path=tmp_path,
                attachment_id=uuid.uuid4().hex[:8],
                original_filename=filename,
                media_type=media_type,
            )

            # Clean up temp file immediately after extraction
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

            if extraction_result.status == "error":
                validation_errors.append(
                    f"File '{filename}': Processing error - {extraction_result.error}"
                )
                continue

            if extraction_result.status == "empty":
                attachment_meta_list.append({
                    "original_filename": filename,
                    "file_size": file_size,
                    "media_type": media_type,
                    "extraction_status": "empty",
                    "extraction_error": extraction_result.error,
                })
                attachment_context_parts.append(
                    f"[Attachment: {filename} - extracted text was empty or contained only non-text content]"
                )
                continue

            # Successful extraction - add to context and metadata
            attachment_meta_list.append({
                "original_filename": extraction_result.original_filename,
                "file_size": extraction_result.file_size,
                "media_type": extraction_result.media_type,
                "extraction_status": "success",
                "page_references": extraction_result.page_references,
                "csv_headers": extraction_result.csv_headers,
            })

            # Add extracted text to the context that will be shared with models
            if extraction_result.text.strip():
                attachment_context_parts.append(
                    f"--- Document: {extraction_result.original_filename} ---\n"
                    f"{extraction_result.text[:2000]}"
                )
                if extraction_result.page_references:
                    attachment_context_parts.append(
                        f"[Source pages: {', '.join(extraction_result.page_references)}]"
                    )

        except Exception as e:
            # Clean up on error
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass
            validation_errors.append(f"File '{filename}': Unexpected error - {str(e)}")

    # Report validation errors if any
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail="Attachment validation failed: " + "; ".join(validation_errors),
        )

    # --- Run the council pipeline (shared runner with persistence) ---
    result = await _run_council_for_request(
        conversation=conversation,
        content=content,
        web_search=web_search,
        context_parts=attachment_context_parts,
        user_extra={
            "has_attachments": len(files) > 0,
            "attachment_count": len(files),
            "attachment_metadata": attachment_meta_list,
            "validation_errors": validation_errors,
        },
    )
    result["metadata"]["attachment_count"] = len(files)
    result["metadata"]["attachment_metadata"] = attachment_meta_list
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
