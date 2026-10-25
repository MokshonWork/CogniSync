"""Tests for the LLM provider abstraction and OpenRouter implementation."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.candidates import Candidate
from backend.providers import OpenRouterProvider


@pytest.mark.asyncio
async def test_generate_success():
    provider = OpenRouterProvider()
    fake_result = {
        "content": "hello world",
        "reasoning_details": None,
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    with patch("backend.providers.query_model_detailed", AsyncMock(return_value=(fake_result, None))) as m:
        cand = await provider.generate("model-x", [{"role": "user", "content": "hi"}])

    assert isinstance(cand, Candidate)
    assert cand.success is True
    assert cand.provider == "openrouter"
    assert cand.model == "model-x"
    assert cand.response == "hello world"
    assert cand.token_usage == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    assert isinstance(cand.duration_ms, int) and cand.duration_ms >= 0
    assert cand.error is None
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_failure_returns_candidate_not_raise():
    provider = OpenRouterProvider()
    from backend.openrouter import OpenRouterError

    with patch(
        "backend.providers.query_model_detailed",
        AsyncMock(return_value=(None, OpenRouterError("rate_limit", "Free quota exhausted for today."))),
    ):
        cand = await provider.generate("model-x", [{"role": "user", "content": "hi"}])

    assert isinstance(cand, Candidate)
    assert cand.success is False
    assert cand.response is None
    assert cand.error == "rate_limit: Free quota exhausted for today."
    assert isinstance(cand.duration_ms, int) and cand.duration_ms >= 0


def test_provider_name():
    assert OpenRouterProvider.name == "openrouter"
