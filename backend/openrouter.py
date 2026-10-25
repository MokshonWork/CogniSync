"""OpenRouter API client for making LLM requests.

`query_model_detailed` returns both the parsed response and a categorized
error, so callers can distinguish authentication, rate-limit, quota,
model availability, validation, timeout, and network failures without
parsing exception strings. 429 handling is bounded: account-level daily
quota exhaustion is terminal (non-retryable, actionable message), transient
limits are retried with Respect-After / exponential backoff + jitter within
an attempt cap and total deadline. `query_model` keeps the original lenient
contract (response dict or None) for compatibility.
"""

import asyncio
import base64
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import (
    OPENROUTER_API_KEY,
    OPENROUTER_API_URL,
    OPENROUTER_MAX_CONCURRENCY,
    RATE_LIMIT_MAX_ATTEMPTS,
    RATE_LIMIT_BACKOFF_CAP_SECONDS,
    RATE_LIMIT_TOTAL_TIMEOUT,
)

# Error categories exposed to callers/UI.
ERR_INVALID_KEY = "invalid_key"
ERR_RATE_LIMIT = "rate_limit"          # transient: may clear on retry
ERR_QUOTA = "quota"                    # account/credit/daily exhaustion: terminal
ERR_MODEL_UNAVAILABLE = "model_unavailable"
ERR_INVALID_RESPONSE = "invalid_response"
ERR_INVALID_REQUEST = "invalid_request"
ERR_TIMEOUT = "timeout"
ERR_NETWORK = "network"
ERR_UNAUTHORIZED_NO_KEY = "unauthorized_no_key"

# Cap concurrent OpenRouter requests process-wide; free tiers have per-minute
# account limits and simultaneous bursts make 429s worse.
_RATE_SEM = asyncio.Semaphore(max(1, OPENROUTER_MAX_CONCURRENCY))

DAILY_QUOTA_MARKER = "free_tier_daily"


class OpenRouterError(Exception):
    """Categorized failure of an OpenRouter request."""

    def __init__(self, category: str, message: str, attempts: int = 1) -> None:
        super().__init__(f"{category}: {message}")
        self.category = category
        self.message = message
        self.attempts = attempts


def _image_to_data_url(file_data: bytes, media_type: str) -> str:
    """Convert image file data to a data URL for OpenRouter multimodal format."""
    b64 = base64.b64encode(file_data).decode("utf-8")
    return f"data:{media_type};base64,{b64}"


def _categorize_status(status: int) -> str:
    if status in (401, 403):
        return ERR_INVALID_KEY
    if status == 429:
        return ERR_RATE_LIMIT
    if status == 402:
        return ERR_QUOTA
    if status in (404, 502, 503):
        return ERR_MODEL_UNAVAILABLE
    if status in (400, 422):
        return ERR_INVALID_REQUEST
    return "upstream_error"


def _parse_429(response: httpx.Response) -> Dict[str, Any]:
    """Safely parse a 429 body/headers into retry policy inputs.

    Secrets-free. Distinguishes account-level free-tier daily quota
    exhaustion (terminal until reset) from transient limits (per-minute or
    provider capacity) that may clear on retry.
    """
    info: Dict[str, Any] = {"retryable": True, "retry_after": None, "detail": ""}

    retry_after_hdr = response.headers.get("retry-after")
    if retry_after_hdr:
        try:
            info["retry_after"] = min(float(retry_after_hdr), RATE_LIMIT_BACKOFF_CAP_SECONDS + 30)
        except (TypeError, ValueError):
            pass

    try:
        body = response.json()
        err = body.get("error") or {}
        metadata = err.get("metadata") or {}
        info["detail"] = str(err.get("message") or "")

        headers_meta = metadata.get("headers") or {}
        reset_raw = response.headers.get("x-ratelimit-reset") or headers_meta.get("X-RateLimit-Reset")
        reset_ms = None
        if reset_raw:
            try:
                reset_ms = float(reset_raw)
            except (TypeError, ValueError):
                reset_ms = None

        remaining = response.headers.get("x-ratelimit-remaining") or headers_meta.get("X-RateLimit-Remaining")
        reset_far = reset_ms is not None and (reset_ms / 1000 - time.time()) > RATE_LIMIT_BACKOFF_CAP_SECONDS

        detail_l = info["detail"].lower()
        is_daily = (
            DAILY_QUOTA_MARKER in str(metadata.get("limit_source") or "")
            or "per-day" in detail_l
            or "per day" in detail_l
            or (remaining == "0" and reset_far)
        )
        if is_daily:
            info["retryable"] = False
            info["reset_epoch"] = reset_ms / 1000 if reset_ms else None
            info["remedy"] = str(metadata.get("remedy_hint") or "")
            if metadata.get("provider_name"):
                info["provider"] = metadata["provider_name"]
        elif reset_ms is not None:
            wait = reset_ms / 1000 - time.time()
            if info["retry_after"] is None and 0 < wait <= RATE_LIMIT_BACKOFF_CAP_SECONDS:
                info["retry_after"] = wait
    except (ValueError, AttributeError, KeyError, TypeError):
        # Unparseable body: bounded optimism — retry with backoff.
        pass
    return info


def _quota_message(info: Dict[str, Any]) -> str:
    """Human, actionable description of account-level exhaustion."""
    when = ""
    reset = info.get("reset_epoch")
    if reset:
        try:
            when = " Resets " + datetime.fromtimestamp(reset, tz=timezone.utc).strftime("%H:%M UTC (UTC midnight).")
        except (OverflowError, OSError, ValueError):
            when = ""
    remedy = f" {info['remedy']}" if info.get("remedy") else ""
    return (
        "OpenRouter free-tier daily request quota is exhausted (per-account limit — applies to every free model)."
        + when
        + remedy
    )


def _transient_message(info: Dict[str, Any], attempts: int) -> str:
    provider = info.get("provider")
    base = "OpenRouter returned HTTP 429"
    if provider:
        base += f" (provider {provider} at capacity)"
    detail = f": {info['detail'][:140]}" if info["detail"] else "."
    return base + detail + f" — retry budget exhausted after {attempts} attempts."


def _error_from_exception(exc: BaseException, attempts: int = 1) -> OpenRouterError:
    if isinstance(exc, httpx.TimeoutException):
        return OpenRouterError(ERR_TIMEOUT, "Request timed out waiting for the model to respond.", attempts)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            info = _parse_429(exc.response)
            if info["retryable"]:
                return OpenRouterError(ERR_RATE_LIMIT, _transient_message(info, attempts), attempts)
            return OpenRouterError(ERR_QUOTA, _quota_message(info), attempts)
        return OpenRouterError(_categorize_status(status), f"OpenRouter returned HTTP {status}.", attempts)
    if isinstance(exc, httpx.RequestError):
        return OpenRouterError(ERR_NETWORK, "Could not reach OpenRouter (network failure).", attempts)
    return OpenRouterError("unknown", "Unexpected failure while calling OpenRouter.", attempts)


def _parse_success(model: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(ERR_INVALID_RESPONSE, "OpenRouter returned an unexpected response body.") from exc
    return {
        "content": message.get("content"),
        "reasoning_details": message.get("reasoning_details"),
        "usage": data.get("usage"),
    }





async def _attempt_once(model: str, messages: List[Dict[str, Any]], timeout: float):
    """One OpenRouter POST under the global concurrency cap. Never retries."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages}
    async with _RATE_SEM:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
        response.raise_for_status()  # outside client context: response body already read
        return _parse_success(model, response.json())


async def query_model_detailed(
    model: str,
    messages: List[Dict[str, Any]],
    timeout: float = 120.0,
    max_attempts: int = RATE_LIMIT_MAX_ATTEMPTS,
) -> Tuple[Optional[Dict[str, Any]], Optional[OpenRouterError]]:
    """Query a single model, returning (result, error). Exactly one is None.

    Retry policy (bounded):
    - only transient 429s are retried (max ``max_attempts`` total attempts,
      whole-call deadline ``RATE_LIMIT_TOTAL_TIMEOUT``),
    - waits short ``Retry-After`` values or exponential backoff + jitter,
    - account-level daily quota exhaustion returns a terminal, actionable
      quota error immediately (waiting hours is never a retry decision the
      request path can make),
    - non-429 errors are never retried,
    - permanent quota/credit errors are never automatically retried.
    """
    if not OPENROUTER_API_KEY:
        return None, OpenRouterError(
            ERR_UNAUTHORIZED_NO_KEY,
            "OPENROUTER_API_KEY is not configured on the server.",
        )

    deadline = time.monotonic() + min(RATE_LIMIT_TOTAL_TIMEOUT, timeout)
    error: Optional[OpenRouterError] = None
    last_429: Dict[str, Any] = {}
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            result = await _attempt_once(model, messages, timeout)
            if result is not None and not isinstance(result, dict):
                raise OpenRouterError(ERR_INVALID_RESPONSE, "OpenRouter returned an unexpected response body.", attempt)
            return result, None
        except OpenRouterError as exc:
            return None, exc  # body-level parse failure: deterministic, no retry
        except Exception as exc:  # noqa: BLE001 — normalized below
            error = _error_from_exception(exc, attempt)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                last_429 = _parse_429(exc.response)

        if error.category != ERR_RATE_LIMIT:
            return None, error

        remaining = deadline - time.monotonic()
        if attempt >= max_attempts or remaining <= 0:
            return None, error

        # Bounded wait: Retry-After when sane, else exponential backoff+jitter.
        wait = last_429.get("retry_after")
        if wait is None:
            wait = min(RATE_LIMIT_BACKOFF_CAP_SECONDS, (2 ** (attempt - 1)) * 2.0) + random.uniform(0, 1.5)
        if wait > remaining:
            return None, OpenRouterError(error.category, error.message, attempt)
        await asyncio.sleep(wait)

    return None, error


async def query_model(
    model: str,
    messages: List[Dict[str, Any]],
    timeout: float = 120.0,
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """Lenient single-model query kept for existing callers/tests.

    Returns the response dict on success, or None on any failure
    (failure details are available via :func:`query_model_detailed`).
    """
    result, _ = await query_model_detailed(model, messages, timeout=timeout)
    return result


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Query multiple models in parallel (gated by the concurrency cap).

    Returns a dict mapping each model id to its response dict, or None if it
    failed. Failures are isolated so one bad model cannot cancel the rest.
    """
    tasks = [query_model(model, messages) for model in models]
    responses = await asyncio.gather(*tasks, return_exceptions=False)
    return {model: response for model, response in zip(models, responses)}
