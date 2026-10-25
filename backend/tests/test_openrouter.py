"""Mocked tests for OpenRouter error categorization, timeouts, and responses."""

import httpx
import time
import pytest
from unittest.mock import AsyncMock, patch

import backend.openrouter as orouter
from backend.openrouter import (
    OpenRouterError,
    query_model_detailed,
    query_model,
    _error_from_exception,
)


def _resp(status, body):
    resp = httpx.Response(status_code=status, request=httpx.Request("POST", "https://x"))
    resp._content = body.encode()
    return resp


@pytest.mark.asyncio
async def test_missing_key_reports_unauthorized_no_key():
    with patch.object(orouter, "OPENROUTER_API_KEY", None):
        result, error = await query_model_detailed("a/b:free", [])
    assert result is None
    assert error.category == orouter.ERR_UNAUTHORIZED_NO_KEY


@pytest.mark.asyncio
async def test_http_401_categorized_as_invalid_key():
    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "401", request=httpx.Request("POST", "https://x"), response=_resp(401, "{}")
        ))
        client_cls.return_value = client
        result, error = await query_model_detailed("a/b:free", [])

    assert result is None
    assert error.category == orouter.ERR_INVALID_KEY
    # user-safe message: no token leakage
    assert "test-key" not in error.message


@pytest.mark.asyncio
async def test_http_429_categorized_as_rate_limit():
    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "429", request=httpx.Request("POST", "https://x"), response=_resp(429, "{}")
        ))
        client_cls.return_value = client
        result, error = await query_model_detailed("a/b:free", [])

    assert error.category == orouter.ERR_RATE_LIMIT


@pytest.mark.asyncio
async def test_timeout_categorized():
    err = _error_from_exception(httpx.ReadTimeout("timed out"))
    assert err.category == orouter.ERR_TIMEOUT


@pytest.mark.asyncio
async def test_success_parses_content_and_usage():
    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        ok = _resp(200, "{}")
        ok.raise_for_status = lambda: None
        ok.json = lambda: {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 3}}
        client.post = AsyncMock(return_value=ok)
        client_cls.return_value = client
        data, error = await query_model_detailed("a/b:free", [])

    assert error is None
    assert data["content"] == "hi"
    assert data["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_malformed_body_categorized_invalid_response():
    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        ok = _resp(200, "{}")
        client.post = AsyncMock(return_value=ok)
        client_cls.return_value = client
        with patch.object(ok, "raise_for_status", lambda: None), \
             patch.object(ok, "json", lambda: {"unexpected": True}):
            result, error = await query_model_detailed("a/b:free", [])

    assert result is None
    assert error.category == orouter.ERR_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_query_model_lenient_wrapper_returns_none_on_failure():
    with patch.object(orouter, "OPENROUTER_API_KEY", None):
        assert await query_model("a/b:free", []) is None


@pytest.mark.asyncio
async def test_parallel_queries_isolate_failures():
    async def fake_detailed(model, messages, timeout=120.0):
        if "bad" in model:
            return None, OpenRouterError(orouter.ERR_MODEL_UNAVAILABLE, "model unavailable")
        return {"content": "ok", "usage": {}}, None

    async def lenient(model, messages, timeout=120.0, **kwargs):
        result, error = await fake_detailed(model, messages, timeout)
        return result

    with patch.object(orouter, "query_model", new=lenient):
        results = await orouter.query_models_parallel(["a/good:free", "a/bad:free"], [])

    assert results["a/good:free"]["content"] == "ok"
    assert results["a/bad:free"] is None


# --- 429 retry policy --------------------------------------------------------


DAILY_QUOTA_BODY = '{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day","code":429,"metadata":{"limit_source":"openrouter_free_tier_daily","remedy_hint":"Wait for the daily reset (see X-RateLimit-Reset), or purchase credits.","provider_name":null}}}'


def _response(status, body, headers=None):
    resp = httpx.Response(status_code=status, request=httpx.Request("POST", "https://x"), headers=headers or {})
    resp._content = body.encode()
    resp.raise_for_status = lambda: (_ for _ in ()).throw(httpx.HTTPStatusError(
        f"{status}", request=httpx.Request("POST", "https://x"), response=resp))
    return resp


@pytest.mark.asyncio
async def test_daily_quota_429_is_terminal_and_actionable():
    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(return_value=_response(429, DAILY_QUOTA_BODY, {
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": str((int(time.time()) + 7200) * 1000),
        }))
        client_cls.return_value = client
        result, error = await query_model_detailed("a/b:free", [])

    assert result is None
    assert error.category == orouter.ERR_QUOTA
    assert error.attempts == 1  # no wasted retries on account-wide exhaustion
    assert "free-tier daily request quota" in error.message
    assert "Resets" in error.message


@pytest.mark.asyncio
async def test_transient_429_with_retry_after_retries_successfully():
    ok = _resp(200, "{}")
    ok.raise_for_status = lambda: None
    ok.json = lambda: {"choices": [{"message": {"content": "hi"}}], "usage": {}}
    responses = [_response(429, '{"error":{"code":429}}', {"retry-after": "0.05"}), ok]
    sleeps = []

    async def fake_sleep(sec):
        sleeps.append(sec)

    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls, \
         patch.object(orouter.asyncio, "sleep", fake_sleep):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(side_effect=responses)
        client_cls.return_value = client
        result, error = await query_model_detailed("a/b:free", [])

    assert result is not None and result["content"] == "hi"
    assert error is None
    assert sleeps  # waited per Retry-After
    assert sleeps[0] <= 1.55  # bounded, never quota-scale


@pytest.mark.asyncio
async def test_transient_429_without_metadata_uses_backoff_and_exhausts():
    call_count = {"n": 0}

    def raise_429(*_a, **_k):
        call_count["n"] += 1
        req = httpx.Request("POST", "https://x")
        raise httpx.HTTPStatusError("429", request=req,
                                    response=_response(429, '{"error":{"code":429}}', {}))

    sleeps = []

    async def fake_sleep(sec):
        sleeps.append(sec)

    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls, \
         patch.object(orouter.asyncio, "sleep", fake_sleep):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(side_effect=raise_429)
        client_cls.return_value = client
        result, error = await query_model_detailed("a/b:free", [], max_attempts=3)

    assert result is None
    assert error.category == orouter.ERR_RATE_LIMIT
    assert call_count["n"] == 3  # exactly max_attempts, no infinite retry
    assert "retry budget exhausted after 3 attempts" in error.message
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential growth with jitter


@pytest.mark.asyncio
async def test_credit_exhaustion_402_never_retried():
    call_count = {"n": 0}

    def raise_402(*_a, **_k):
        call_count["n"] += 1
        req = httpx.Request("POST", "https://x")
        raise httpx.HTTPStatusError("402", request=req,
                                    response=_response(402, '{"error":{"code":402}}', {}))

    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(side_effect=raise_402)
        client_cls.return_value = client
        result, error = await query_model_detailed("paid/x", [])

    assert result is None
    assert error.category == orouter.ERR_QUOTA
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_non_rate_limit_errors_never_retried():
    call_count = {"n": 0}

    def raise_500(*_a, **_k):
        call_count["n"] += 1
        req = httpx.Request("POST", "https://x")
        raise httpx.HTTPStatusError("500", request=req, response=_response(500, "{}", {}))

    with patch.object(orouter, "OPENROUTER_API_KEY", "test-key"), \
         patch.object(orouter.httpx, "AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.post = AsyncMock(side_effect=raise_500)
        client_cls.return_value = client
        result, error = await query_model_detailed("a/b:free", [])

    assert error.category == "upstream_error"
    assert call_count["n"] == 1
