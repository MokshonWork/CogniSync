"""3-stage LLM Council orchestration."""

import asyncio
import os
from typing import List, Dict, Any, Optional, Tuple
from .candidates import Candidate
from .openrouter import query_model
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL, FALLBACK_FREE_MODEL_IDS
from .ddg_search import (
    DuckDuckGoSearchService,
    SEARCH_EVIDENCE_MARKER,
    SEARCH_EVIDENCE_SYSTEM_PROMPT,
    format_search_context,
)


def build_stage1_messages(content: str) -> List[Dict[str, str]]:
    """User content plus a trusted system boundary when it contains search evidence."""
    messages = [{"role": "user", "content": content}]
    if SEARCH_EVIDENCE_MARKER in content:
        messages.insert(0, {"role": "system", "content": SEARCH_EVIDENCE_SYSTEM_PROMPT})
    return messages


# Fallback policy: only these failure categories may try a verified-free
# alternative model. Daily/quota exhaustion is account-wide — another free
# model cannot help and must NOT be silently substituted.
FALLBACKABLE_CATEGORIES = ("rate_limit", "model_unavailable", "timeout", "network")


def _build_provider(results=None) -> Any:
    """Build the provider used by council stages.

    With ``results`` supplied (tests inject a scripted sequence), returns a
    fake provider returning those results in order. Without results (the
    production path) returns the real OpenRouter-backed provider.
    """
    if results is not None:

        class _FakeProvider:
            name = "fake"

            def __init__(self, results):
                self.results = list(results) if results else []
                self.calls = []

            async def generate(self, model, messages, **kwargs):
                self.calls.append((model, messages))
                if not self.results:
                    return None
                idx = len(self.calls) - 1
                return self.results[idx] if idx < len(self.results) else None

        return _FakeProvider(results)

    from .providers import OpenRouterProvider

    return OpenRouterProvider()


def _successful(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter results to only include successful ones.

    Args:
        results: List of result dicts with 'success' key.

    Returns:
        List of result dicts where success is True and response is non-empty.
    """
    return [r for r in results if r.get("success") is True and r.get("response", "") != ""]


async def _generate_with_fallback(
    provider,
    model_id: str,
    messages: List[Dict[str, Any]],
    alternatives: Optional[Dict[str, List[str]]],
    used: set,
):
    """Generate for model_id; on a fallbackable failure try one alternative.

    Returns (candidate, fallback_id_or_None). Alternatives are caller-
    supplied and catalog-verified free ids; ``used`` prevents two council
    slots from collapsing onto the same substitute. Quota errors are never
    substituted — the original failure stands.
    """
    candidate = await provider.generate(model_id, messages)
    if not isinstance(candidate, Candidate) or getattr(candidate, "success", False):
        return candidate, None, None
    if (getattr(candidate, "error_category", None) or "") not in FALLBACKABLE_CATEGORIES:
        return candidate, None, None

    for alt in (alternatives or {}).get(model_id, []):
        alt = (alt or "").strip()
        if not alt or alt == model_id or alt in used:
            continue
        used.add(alt)
        alt_candidate = await provider.generate(alt, messages)
        if isinstance(alt_candidate, Candidate) and getattr(alt_candidate, "success", False):
            return alt_candidate, alt, f"{candidate.error_category}: {candidate.error}"
        # alternative failed too: record its failure under this slot
        return alt_candidate, None, None
    return candidate, None, None


def _usage_counts(usage) -> tuple[int, int | None]:
    """Return (total, output) provider tokens; output is None when unreported."""
    if not isinstance(usage, dict):
        return 0, None
    total = usage.get("total_tokens")
    output = usage.get("completion_tokens")
    return (
        total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else 0,
        output if isinstance(output, int) and not isinstance(output, bool) and output >= 0 else None,
    )


async def stage1_collect_responses(
    user_query: str,
    search_context: str = "",
    models: List[str] = None,
    provider=None,
    alternatives: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """
    Stage 1: Collect individual responses from all council models.

    Args:
        user_query: The user's question
        search_context: Optional web search context to include
        models: Model ids to consult. Defaults to the configured council
            members (free fallbacks); callers resolved them against the
            OpenRouter catalog.
        provider: Override for tests. Defaults to the real provider.

    Returns:
        List of dicts with 'model' and 'response' keys — including failed
        models, which settle with success=False and an error instead of
        hanging.
    """
    # Build message content with optional search context
    # For base case (no search): just the user query
    # For search case: search context first, then user question
    if search_context:
        content = f"{search_context}\n\nUser question: {user_query}"
    else:
        content = user_query

    messages = build_stage1_messages(content)

    model_ids = list(models) if models else list(COUNCIL_MODELS)
    provider = provider if provider is not None else _build_provider()

    # Query each model in parallel; every model settles (success or failure).
    used: set = set(model_ids)
    pairs = await asyncio.gather(
        *[_generate_with_fallback(provider, m, messages, alternatives, used) for m in model_ids]
    )

    stage1_results = []
    for model, (response, fallback_used, original_error) in zip(model_ids, pairs):
        if response is None:
            # Defensive: provider contract says no None, but never hang on it.
            stage1_results.append({
                "model": model,
                "provider": "openrouter",
                "success": False,
                "response": None,
                "error": "Provider returned no result.",
                "duration_ms": 0,
                "error_category": None,
                "fallback_used": fallback_used,
                "error_original": original_error,
            })
            continue
        if hasattr(response, 'response'):
            # Candidate object
            usage = getattr(response, 'token_usage', None) or {}
            tokens, output_tokens = _usage_counts(usage)
            stage1_results.append({
                "model": model,
                "provider": getattr(response, 'provider', 'openrouter'),
                "success": response.success,
                "response": response.response,
                "error": getattr(response, 'error', None),
                "duration_ms": getattr(response, 'duration_ms', 0) or 0,
                "tokens": tokens,
                "output_tokens": output_tokens,
                "error_category": getattr(response, 'error_category', None),
                "fallback_used": fallback_used,
                "error_original": original_error,
            })
        else:
            # Plain dict from a scripted provider
            stage1_results.append({
                "model": model,
                "provider": response.get('provider', 'openrouter'),
                "success": response.get('success', True),
                "response": response.get('response', response.get('content', '')),
                "error": response.get('error', None),
                "duration_ms": response.get('duration_ms', 0) or 0,
                "tokens": response.get('tokens', 0) or 0,
                "output_tokens": response.get('output_tokens', response.get('completion_tokens')),
                "error_category": response.get('error_category'),
                "fallback_used": fallback_used,
                "error_original": original_error,
            })

    return stage1_results


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    provider=None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1

    Returns:
        Tuple of (rankings list, label_to_model mapping)
    """
    # Create anonymized labels for responses (Response A, Response B, etc.)
    # Only successful Stage 1 answers are ranked; failed models have no
    # response to anonymize and must not appear in the ranking prompt.
    ranked_results = [r for r in stage1_results if r.get("success") is True]
    labels = [chr(65 + i) for i in range(len(ranked_results))]  # A, B, C, ...

    # Create mapping from label to model name
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, ranked_results)
    }

    # Build the ranking prompt
    if not ranked_results:
        return [], {}

    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, ranked_results)
    ])

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]

    # Only models that produced answers in Stage 1 review (a failed model
    # has nothing to rank and must not be billed for a ranking request).
    reviewer_models = [
        result["model"]
        for result in stage1_results
        if result.get("success") is True
    ]

    provider = provider if provider is not None else _build_provider()
    candidates = await asyncio.gather(
        *[provider.generate(m, messages) for m in reviewer_models]
    )

    # Format results — failures are recorded so reviewers never hang.
    stage2_results = []
    for model, response in zip(reviewer_models, candidates):
        entry = {
            "model": model,
            "success": True,
            "error": None,
        }
        full_text = ""
        if response is None:
            entry.update({"success": False, "error": "Provider returned no result.", "ranking": None, "parsed_ranking": []})
            stage2_results.append(entry)
            continue
        if hasattr(response, 'response'):
            full_text = response.response
            if not response.success:
                entry.update({"success": False, "error": getattr(response, 'error', None) or "Ranking request failed", "ranking": None, "parsed_ranking": []})
                stage2_results.append(entry)
                continue
        elif hasattr(response, 'get'):
            full_text = response.get('content', '')
            if response.get('success') is False:
                entry.update({"success": False, "error": response.get('error', None), "ranking": None, "parsed_ranking": []})
                stage2_results.append(entry)
                continue
        else:
            full_text = str(response)
        entry.update({
            "ranking": full_text,
            "parsed_ranking": parse_ranking_from_text(full_text),
        })
        stage2_results.append(entry)

    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    chairman_model: Optional[str] = None,
    provider=None,
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2
        chairman_model: Model id to synthesize with. Defaults to the
            configured chairman.
        provider: Override for tests. Defaults to the real provider.

    Returns:
        Dict with 'model', 'response', 'success' and 'error' keys —
        synthesis failure is reported, never silent.
    """
    # Build comprehensive context for chairman
    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])

    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    messages = [{"role": "user", "content": chairman_prompt}]

    # Build provider using _build_provider for test mocking compatibility
    provider = provider if provider is not None else _build_provider()

    chosen_chairman = chairman_model or CHAIRMAN_MODEL
    chairman_response = await provider.generate(chosen_chairman, messages)

    # Retry once with a model that already succeeded in Stage 1 when the
    # configured chairman fails (e.g. currently rate-limited free model).
    failed_chairman = chosen_chairman
    if chairman_response is not None and getattr(chairman_response, "success", True) is False:
        fallbacks = [r["model"] for r in stage1_results if r.get("success") and r["model"] != chosen_chairman]
        if fallbacks:
            failed_chairman = chosen_chairman
            chosen_chairman = fallbacks[0]
            chairman_response = await provider.generate(chosen_chairman, messages)

    if chairman_response is None:
        # Fallback if chairman fails
        return {
            "model": failed_chairman,
            "response": "Error: Unable to generate final synthesis.",
            "success": False,
            "error": "The chairman model returned no result. Successful model answers are preserved above.",
        }

    candidate_error = getattr(chairman_response, 'error', None)
    candidate_success = getattr(chairman_response, 'success', True)
    if hasattr(chairman_response, 'get'):
        candidate_error = chairman_response.get('error', None)
        candidate_success = chairman_response.get('success', True)
        response_text = chairman_response.get('content', '')
    else:
        response_text = chairman_response.response

    if candidate_success is False:
        return {
            "model": chosen_chairman,
            "response": "Error: Unable to generate final synthesis.",
            "success": False,
            "error": candidate_error or "Chairman synthesis request failed.",
        }

    return {
        "model": chosen_chairman,
        "response": response_text,
        "success": True,
        "error": None,
    }


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.

    Args:
        ranking_text: The full text response from the model

    Returns:
        List of response labels in ranked order
    """
    import re

    # Handle None or empty input
    if not ranking_text:
        return []

    # Look for "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        # Extract everything after "FINAL RANKING:"
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            # Try to extract numbered list format (e.g., "1. Response A")
            # This pattern looks for: number, period, optional space, "Response X"
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                # Extract just the "Response X" part
                return [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]

            # Fallback: Extract all "Response X" patterns in order
            matches = re.findall(r'Response [A-Z]', ranking_section)
            return matches

    # Fallback: try to find any "Response X" patterns in order
    matches = re.findall(r'Response [A-Z]', ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Calculate aggregate rankings across all models.

    Args:
        stage2_results: Rankings from each model
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        List of dicts with model name and average rank, sorted best to worst
    """
    from collections import defaultdict

    # Track positions for each model
    model_positions = defaultdict(list)

    for ranking in stage2_results:
        ranking_text = ranking['ranking']

        # Parse the ranking from the structured format
        parsed_ranking = parse_ranking_from_text(ranking_text)

        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    # Calculate average position for each model
    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions)
            })

    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x['average_rank'])

    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """
    Generate a short title for a conversation based on the first user message.

    Args:
        user_query: The first user message

    Returns:
        A short title (3-5 words)
    """
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]

    # Use a configured free fallback model for cheap title generation
    title_model = os.getenv("TITLE_MODEL") or FALLBACK_FREE_MODEL_IDS[0]
    response = await query_model(title_model, messages, timeout=30.0)

    if response is None:
        # Fallback to a generic title
        return "New Conversation"

    title = response.get('content', 'New Conversation').strip()

    # Clean up the title - remove quotes, limit length
    title = title.strip('"\'')

    # Truncate if too long
    if len(title) > 50:
        title = title[:47] + "..."

    return title


async def run_full_council(
    user_query: str,
    web_search: bool = False,
    models: Optional[List[str]] = None,
    chairman_model: Optional[str] = None,
    alternatives: Optional[Dict[str, List[str]]] = None,
) -> Tuple[List, List, Dict, Dict]:
    """
    Run the complete 3-stage council process.

    Args:
        user_query: The user's question
        web_search: Whether to enable web search
        models: Model ids resolved by the caller (catalog-verified free
            models). Defaults to the configured council fallback list.
        chairman_model: Chairman model id. Defaults to configuration.

    Returns:
        Tuple of (stage1_results, stage2_results, stage3_result, metadata)
    """
    # Stage 1: Collect individual responses
    search_context = ""
    search_status = "unavailable"  # default status
    search_error = None  # default error (None means no error)
    if web_search:
        search_service = DuckDuckGoSearchService()
        search_response = await search_service.search(user_query)
        search_context = format_search_context(search_response)
        if search_context:
            search_status = "success"
        elif search_response.status == "no_results":
            search_status = "no_results"
            search_error = search_response.error_message if hasattr(search_response, 'error_message') else None
        else:
            search_status = search_response.status  # unavailable, error, timeout, etc.
            search_error = search_response.error_message if hasattr(search_response, 'error_message') else None
    stage1_results = await stage1_collect_responses(user_query, search_context=search_context if search_context else "", models=models, alternatives=alternatives)

    # If no models responded successfully, return error
    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again.",
            "success": False,
            "error": "None of the council models completed Stage 1.",
        }, {}

    # If every Stage 1 attempt failed, report per-model errors honestly.
    if not any(r.get("success") for r in stage1_results):
        if all((r.get("error_category") or "") == "quota" for r in stage1_results):
            fail_reason = ("Every council model hit the OpenRouter free-tier daily request quota — "
                           "it is per-account and resets at UTC midnight. Wait for the reset or add credits "
                           "to unlock more free requests; retrying now cannot succeed.")
        else:
            fail_reason = ("All council model requests failed — per-model errors are on the cards. "
                           "Transient rate limits were retried; check the individual messages.")
        return stage1_results, [], {
            "model": "error",
            "response": "All models failed to respond. Please try again.",
            "success": False,
            "error": fail_reason,
        }, {"search": {"enabled": web_search}}

    # Stage 2: Collect rankings
    stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results)

    # Calculate aggregate rankings
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    # Stage 3: Synthesize final answer
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
        chairman_model=chairman_model,
    )

    # Prepare metadata
    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }

    # Add search status to metadata based on web_search flag and search result
    if web_search:
        result_count = len(search_response.results) if search_response.results else 0
        metadata["search"] = {"enabled": True, "status": search_status, "result_count": result_count}
        if search_error:
            metadata["search"]["error_message"] = search_error
    else:
        metadata["search"] = {"enabled": False}

    return stage1_results, stage2_results, stage3_result, metadata


async def run_council_events(
    user_query: str,
    models: Optional[List[str]] = None,
    chairman_model: Optional[str] = None,
    provider=None,
    alternatives: Optional[Dict[str, List[str]]] = None,
):
    """
    Async generator producing progressive council events.

    Stage 1 answers stream as they complete (asyncio.as_completed), Stage 2
    reviews stream per reviewer, aggregate rankings are emitted once known,
    and Stage 3 synthesis is emitted with the genuine chairman output (with
    the same one-shot fallback to a Stage 1 success as the blocking path).

    Event sequence:
        stage1_start {models}
        stage1_result {model, success, response|error, tokens, duration_ms}   (per model)
        run_failed    {stage1}                                                 (only if all Stage 1 failed)
        stage2_start  {reviewers}
        stage2_result {model, review, parsed_ranking, success, error}          (per reviewer)
        rankings      {aggregate, label_to_model}
        stage3_start  {chairman}
        stage3_result {stage3}

    Caller is responsible for persistence; events carry everything needed.
    """
    model_ids = list(dict.fromkeys(m.strip() for m in models if m.strip())) if models else list(COUNCIL_MODELS)
    provider = provider if provider is not None else _build_provider()

    # --- Stage 1: independent responses, streamed as each finishes ---
    yield {"type": "stage1_start", "models": model_ids}
    stage1_results: List[Dict[str, Any]] = []

    used: set = set(model_ids)
    coros = [
        _generate_with_fallback(provider, m, build_stage1_messages(user_query), alternatives, used)
        for m in model_ids
    ]
    pending = {asyncio.ensure_future(c): m for c, m in zip(coros, model_ids)}

    def _stage1_entry(model_id: str, pair) -> Dict[str, Any]:
        response, fallback_used, original_error = pair
        if response is None:
            return {"model": model_id, "provider": "openrouter", "success": False,
                    "response": None, "error": "Provider returned no result.", "duration_ms": 0,
                    "tokens": 0, "output_tokens": None,
                    "fallback_used": fallback_used, "error_original": original_error}
        if hasattr(response, "response"):
            usage = getattr(response, "token_usage", None) or {}
            tokens, output_tokens = _usage_counts(usage)
            return {"model": model_id,
                    "provider": getattr(response, "provider", "openrouter"),
                    "success": response.success,
                    "response": response.response,
                    "error": getattr(response, "error", None),
                    "duration_ms": getattr(response, "duration_ms", 0) or 0,
                    "tokens": tokens,
                    "output_tokens": output_tokens,
                    "error_category": getattr(response, "error_category", None),
                    "fallback_used": fallback_used, "error_original": original_error}
        return {"model": model_id, "provider": response.get("provider", "openrouter"),
                "success": response.get("success", True),
                "response": response.get("response", response.get("content", "")),
                "error": response.get("error"), "duration_ms": response.get("duration_ms", 0) or 0,
                "tokens": response.get("tokens", 0) or 0,
                "output_tokens": response.get("output_tokens", response.get("completion_tokens")),
                "fallback_used": fallback_used, "error_original": original_error}

    while pending:
        done = await asyncio.wait({k for k in pending}, return_when=asyncio.FIRST_COMPLETED)
        for fut in done[0]:
            model_id = pending.pop(fut)
            try:
                entry = _stage1_entry(model_id, fut.result())
            except Exception as exc:  # never let one provider crash the stream
                entry = {"model": model_id, "provider": "openrouter", "success": False,
                         "response": None, "error": f"Provider request failed: {str(exc)[:200]}",
                         "duration_ms": 0, "tokens": 0}
            stage1_results.append(entry)
            yield {"type": "stage1_result", **entry}

    if not any(r.get("success") for r in stage1_results):
        if all((r.get("error_category") or "") == "quota" for r in stage1_results):
            reason = ("Every council model hit the OpenRouter free-tier daily request quota — "
                      "it is per-account and resets at UTC midnight. Wait for the reset or add credits "
                      "to unlock more free requests; retrying now cannot succeed.")
        else:
            reason = ("All council model requests failed — per-model errors are on the cards. "
                      "Transient rate limits were retried; check the individual messages.")
        yield {"type": "run_failed", "stage1": stage1_results, "error": reason}
        return

    # --- Stage 2: anonymized peer review, streamed per reviewer ---
    ranked_results = [r for r in stage1_results if r.get("success") is True]
    labels = [chr(65 + i) for i in range(len(ranked_results))]
    label_to_model = {
        f"Response {label}": result["model"]
        for label, result in zip(labels, ranked_results)
    }

    responses_text = "\n\n".join(
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, ranked_results)
    )
    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...

FINAL RANKING:
1. Response B
2. Response A

Now provide your evaluation and ranking:"""

    reviewer_models = [r["model"] for r in ranked_results]
    yield {"type": "stage2_start", "reviewers": reviewer_models}
    stage2_results: List[Dict[str, Any]] = []

    review_coros = [provider.generate(m, [{"role": "user", "content": ranking_prompt}]) for m in reviewer_models]
    review_pending = {asyncio.ensure_future(c): m for c, m in zip(review_coros, reviewer_models)}
    while review_pending:
        done, _ = await asyncio.wait({k for k in review_pending}, return_when=asyncio.FIRST_COMPLETED)
        for fut in done:
            model_id = review_pending.pop(fut)
            try:
                response = fut.result()
            except Exception as exc:
                entry = {"model": model_id, "success": False,
                         "error": f"Review request failed: {str(exc)[:200]}",
                         "ranking": None, "parsed_ranking": []}
            else:
                full_text = ""
                entry = {"model": model_id, "success": True, "error": None}
                if response is None:
                    entry.update({"success": False, "error": "Provider returned no result.", "ranking": None, "parsed_ranking": []})
                elif hasattr(response, "response"):
                    full_text = getattr(response, "response", "") or ""
                    if getattr(response, "success", True) is False:
                        entry.update({"success": False, "error": getattr(response, "error", None) or "Ranking request failed",
                                      "ranking": None, "parsed_ranking": []})
                    else:
                        entry.update({"ranking": full_text, "parsed_ranking": parse_ranking_from_text(full_text)})
                elif hasattr(response, "get"):
                    if response.get("success") is False:
                        entry.update({"success": False, "error": response.get("error"), "ranking": None, "parsed_ranking": []})
                    else:
                        full_text = response.get("content", "") or ""
                        entry.update({"ranking": full_text, "parsed_ranking": parse_ranking_from_text(full_text)})
                else:
                    full_text = str(response)
                    entry.update({"ranking": full_text, "parsed_ranking": parse_ranking_from_text(full_text)})
            stage2_results.append(entry)
            yield {"type": "stage2_result", **entry}

    aggregate = calculate_aggregate_rankings(stage2_results, label_to_model)
    yield {"type": "rankings", "aggregate": aggregate, "label_to_model": label_to_model}

    # --- Stage 3: chairman synthesis on genuine stage outputs ---
    chosen_chairman = chairman_model or CHAIRMAN_MODEL
    yield {"type": "stage3_start", "chairman": chosen_chairman}

    stage1_text = "\n\n".join(f"Model: {r['model']}\nResponse: {r['response']}" for r in ranked_results)
    stage2_text = "\n\n".join(
        f"Model: {r['model']}\nRanking: {r['ranking'] if r.get('ranking') else (r.get('error') or '(review failed)')}"
        for r in stage2_results
    )
    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    chairman_response = await provider.generate(chosen_chairman, [{"role": "user", "content": chairman_prompt}])

    failed_chairman = chosen_chairman
    if chairman_response is not None and getattr(chairman_response, "success", True) is False:
        fallbacks = [r["model"] for r in stage1_results if r.get("success") and r["model"] != chosen_chairman]
        if fallbacks:
            failed_chairman = chosen_chairman
            chosen_chairman = fallbacks[0]
            chairman_response = await provider.generate(chosen_chairman, [{"role": "user", "content": chairman_prompt}])

    stage3_result: Dict[str, Any]
    if chairman_response is None:
        stage3_result = {"model": failed_chairman, "response": "Error: Unable to generate final synthesis.",
                         "success": False,
                         "error": "The chairman model returned no result. Successful model answers are preserved above."}
    elif getattr(chairman_response, "success", True) is False:
        stage3_result = {"model": chosen_chairman, "response": "Error: Unable to generate final synthesis.",
                         "success": False,
                         "error": (getattr(chairman_response, "error", None) or "Chairman synthesis request failed.")}
    else:
        stage3_result = {"model": chosen_chairman, "response": chairman_response.response,
                         "success": True, "error": None}

    yield {"type": "stage3_result", "stage3": stage3_result}
