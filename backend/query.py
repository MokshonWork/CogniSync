"""One-shot council query endpoint logic for POST /api/query."""

import time
import asyncio
from typing import Any, Dict, List

from .openrouter import query_model_detailed
from .config import FREE_TESTING_MODE
from .model_catalog import get_catalog, is_free_testing_eligible, resolve_council_models
from .council import build_stage1_messages, parse_ranking_from_text
from .ddg_search import DuckDuckGoSearchService, format_search_context


def _error_desc(error) -> str:
    """Flat user-safe error string from an OpenRouterError-like object."""
    if error is None:
        return "Request failed."
    return f"{getattr(error, 'category', 'error')}: {getattr(error, 'message', 'Request failed.')}"


async def _timed_query(model_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Query a model, measuring wall time, token usage and failure reason."""
    start = time.monotonic()
    result, error = await query_model_detailed(model_id, messages)
    duration_ms = int((time.monotonic() - start) * 1000)
    if result is None:
        return {
            "model": model_id,
            "success": False,
            "text": "",
            "time_ms": duration_ms,
            "tokens": 0,
            "error": _error_desc(error),
        }
    usage = result.get("usage") or {}
    return {
        "model": model_id,
        "success": True,
        "text": result.get("content") or "",
        "time_ms": duration_ms,
        "tokens": usage.get("total_tokens") or 0,
        "error": None,
    }


def _snippet(text: str, limit: int = 240) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _aggregate_reviews(
    stage2: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    name_of: Dict[str, str],
) -> Dict[str, Any]:
    """Count first-place votes from parsed peer rankings.

    Returns the majority-winning model, per-model vote counts, and any
    reviewer whose first pick differed from the majority.
    """
    firsts: Dict[str, List[str]] = {}  # chosen model id -> reviewer model ids
    for review in stage2:
        if review.get("success") is False:
            continue
        ranked = parse_ranking_from_text(review["text"])
        if ranked and ranked[0] in label_to_model:
            firsts.setdefault(label_to_model[ranked[0]], []).append(review["model"])
    if not firsts:
        return {"majority": None, "votes": {}, "dissenters": []}

    majority = max(firsts, key=lambda m: len(firsts[m]))
    dissenters = [
        {"model": name_of[model_id], "reviewer": name_of[reviewer]}
        for model_id in firsts
        if model_id != majority
        for reviewer in firsts[model_id]
    ]
    return {"majority": majority, "votes": firsts, "dissenters": dissenters}


async def resolve_selected(models: List[str]) -> Dict[str, Any]:
    """Resolve requested model ids against the catalog.

    ``models`` may be empty (auto-select free models) or contain model ids.
    In free-testing mode, paid ids are rejected with a clear error. Returns a
    dict {"ids": [...], "names": {...}, "fallback": bool, "deficient": bool}.
    """
    catalog, fallback = await get_catalog()
    req = [m for m in (models or []) if isinstance(m, str) and m.strip()]
    if req:
        if FREE_TESTING_MODE:
            ineligible = [m for m in req if not is_free_testing_eligible(m, catalog)]
            if ineligible:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Free-testing mode: these models are not currently free and will not be billed: "
                        f"{', '.join(ineligible)}. Provide ':free' model ids or clear the selection to "
                        "auto-pick free models."
                    ),
                )
        ids = []
        for m in req:
            if m not in ids:
                ids.append(m)
        selected = [c for c in catalog if c["id"] in ids]
        names = {c["id"]: c["name"] for c in selected}
        return {"ids": ids, "names": names, "fallback": fallback, "deficient": False}
    resolved = await resolve_council_models()
    return resolved


async def run_query(
    question: str,
    model_keys: List[str] = None,
    mode: str = "balanced",
    web_search: bool = False,
) -> Dict[str, Any]:
    """Run the council pipeline for a single question.

    model_keys may be empty (auto-select) or a list of OpenRouter model ids;
    in free-testing mode non-free ids are rejected before any request is
    billed.

    Stage 1: selected models answer in parallel (timed, token-counted, with
      categorized per-model errors).
    Stage 2: anonymized peer review for the discussion and vote tally.
    Stage 3: chairman synthesizes the final answer.

    Returns {"model_responses": [...], "synthesis": str, "synthesis_error":
    str|None, "discussion": [...], "search": {...}, "models_fallback": bool}
    """
    resolved = await resolve_selected(model_keys)
    ids = resolved["ids"]
    name_of = dict(resolved["names"])

    # Optional server-side DuckDuckGo grounding (no key, never exposed)
    search_meta: Dict[str, Any] = {"enabled": web_search}
    effective_question = question
    if web_search:
        service = DuckDuckGoSearchService()
        search_response = await service.search(question)
        search_meta.update({"status": search_response.status})
        if search_response.error_message:
            search_meta["error"] = search_response.error_message
        if search_response.results:
            search_meta["result_count"] = len(search_response.results)
        context = format_search_context(search_response)
        if context:
            effective_question = f"{context}\n\nUser question: {question}"

    # Stage 1 - independent answers
    messages = build_stage1_messages(effective_question)
    stage1 = [] if not ids else await asyncio.gather(*[_timed_query(mid, messages) for mid in ids])
    ok = [r for r in stage1 if r["success"] and r["text"]]
    model_responses = [
        {
            "model": name_of.get(r["model"], r["model"]),
            "model_id": r["model"],
            "time_ms": r["time_ms"],
            "tokens": r["tokens"],
            "text": r["text"],
            "success": r["success"],
            "error": r.get("error"),
        }
        for r in stage1
    ]
    if not ok:
        return {
            "model_responses": model_responses,
            "synthesis": "",
            "synthesis_error": "All council models failed. Check per-model errors above.",
            "discussion": [],
            "search": search_meta,
            "models_fallback": resolved["fallback"],
        }

    # Stage 2 - anonymized peer review (feeds discussion + votes)
    letters = "ABCDEFGHIJ"
    anon_text = "\n\n".join(f"Response {letters[i]}:\n{r['text']}" for i, r in enumerate(ok))
    label_to_model = {f"Response {letters[i]}": r["model"] for i, r in enumerate(ok)}
    ranking_prompt = (
        "Question:\n" + effective_question + "\n\nResponses from different models (anonymized):\n\n" + anon_text +
        "\n\nEvaluate the responses briefly, then end with exactly this format:\n"
        "FINAL RANKING:\n1. Response X\n2. Response Y\n3. ...\n"
        "Rank by accuracy and insight. No text after the ranking."
    )
    review_results = await asyncio.gather(*[_timed_query(mid, [{"role": "user", "content": ranking_prompt}]) for mid in ids])

    agg = _aggregate_reviews(review_results, label_to_model, name_of)

    # Stage 3 - chairman synthesis
    context = "\n\n".join(f"Model: {name_of.get(r['model'], r['model'])}\n{r['text']}" for r in ok)
    votes_text = "\n".join(f"{name_of[m]}: {len(rs)} first-place votes" for m, rs in agg["votes"].items())
    chairman_prompt = (
        "You are the Chairman of an LLM Council. Synthesize the following model responses "
        "into one clear, well-reasoned answer to the user's question. Weigh the peer vote tally.\n\n"
        f"Question: {effective_question}\n\nModel responses:\n{context}\n\nPeer votes:\n{votes_text}\n\n"
        "Final answer:"
    )
    from .config import CHAIRMAN_MODEL

    chairman_result, chairman_error = await query_model_detailed(CHAIRMAN_MODEL, [{"role": "user", "content": chairman_prompt}])
    synthesis = (chairman_result or {}).get("content") or ""
    synthesis_error = None if chairman_result else (_error_desc(chairman_error) if chairman_error else "Chairman synthesis failed and returned no content.")

    # Build discussion timeline from real stage 2/3 data
    total = len(ok)
    majority_name = name_of.get(agg["majority"]) if agg["majority"] else None
    majority_model = next((r for r in ok if r["model"] == agg["majority"]), None)
    dissent_text = (
        "; ".join(f"{d['reviewer']} ranked {d['model']} first" for d in agg["dissenters"][:3])
        if agg["dissenters"]
        else "No significant disagreement on which response was strongest."
    )
    discussion = [
        {"type": "Agreement", "text": (
            f"{len(agg['votes'].get(agg['majority'], []))} of {total} reviewers ranked {majority_name} first."
            if majority_name else "Reviewers completed their peer evaluations."
        )},
        {"type": "Different view", "text": dissent_text},
        {"type": "Additional insight", "text": (
            f"{majority_name} added: " + _snippet(majority_model["text"], 200)
            if majority_model else ""
        )},
        {"type": "Synthesis", "text": _snippet(synthesis, 280) if synthesis else (f"Chairman synthesis failed: {synthesis_error}" )},
    ]

    return {
        "model_responses": model_responses,
        "final_answer": synthesis or None,
        "synthesis": synthesis,
        "synthesis_error": synthesis_error,
        "discussion": discussion,
        "search": search_meta,
        "models_fallback": resolved["fallback"],
    }
