"""Mocked tests for OpenRouter free-model catalog discovery and selection."""

import httpx
import pytest
from unittest.mock import AsyncMock, patch

import backend.model_catalog as mc
from backend.model_catalog import (
    _is_free,
    _parse_catalog,
    get_catalog,
    resolve_council_models,
)


CATALOG_BODY = {
    "data": [
        # verified free: zero pricing + ':free' id
        {"id": "deepseek/deepseek-chat-v3.1:free", "name": "DeepSeek: DeepSeek V3.1 (free)", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 64000},
        # ':free' id without pricing metadata
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Meta: Llama 3.3 70B (free)", "context_length": 64000},
        # free family but this specific id is PAID — must be excluded
        {"id": "openai/gpt-4o", "name": "OpenAI: GPT-4o", "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
        # zero pricing without ':free' id — verified free via pricing
        {"id": "google/gemma-3-27b-it:free", "name": "Google: Gemma 3 27B (free)", "pricing": {"prompt": "0", "completion": "0"}},
        # ':free' id but positive price (should never happen, but must fail closed)
        {"id": "evil/billed:free", "name": "Evil: billed free-lite", "pricing": {"prompt": "0.0001", "completion": "0"}},
    ]
}


def test_is_free_requires_verified_zero_pricing_or_free_variants():
    assert _is_free({"id": "a/b:free", "pricing": {"prompt": "0", "completion": "0"}}) is True
    assert _is_free({"id": "a/b:free", "pricing": {}}) is True
    assert _is_free({"id": "a/b", "pricing": {"prompt": "0", "completion": "0"}}) is True
    assert _is_free({"id": "a/b", "pricing": {"prompt": "0.001", "completion": "0"}}) is False
    # billed ':free' variant fails closed on positive pricing
    assert _is_free({"id": "a/b:free", "pricing": {"prompt": "0.0001", "completion": "0"}}) is False


def test_parse_catalog_excludes_paid_models():
    free = _parse_catalog(CATALOG_BODY)
    ids = [m["id"] for m in free]
    assert "deepseek/deepseek-chat-v3.1:free" in ids
    assert "meta-llama/llama-3.3-70b-instruct:free" in ids
    assert "google/gemma-3-27b-it:free" in ids
    # never assume free from provider name; paid and billed excluded
    assert "openai/gpt-4o" not in ids
    assert "evil/billed:free" not in ids


def test_parse_catalog_display_name_strips_free_suffix():
    free = _parse_catalog(CATALOG_BODY)
    by_id = {m["id"]: m for m in free}
    assert by_id["deepseek/deepseek-chat-v3.1:free"]["name"] == "DeepSeek: DeepSeek V3.1"


@pytest.mark.asyncio
async def test_get_catalog_caches_and_refreshes():
    mc._cache["models"] = None
    fetch = AsyncMock(return_value=_parse_catalog(CATALOG_BODY))
    with patch.object(mc, "fetch_catalog", fetch), \
         patch.object(mc, "MODEL_CATALOG_TTL_SECONDS", 600):
        models, fb1 = await get_catalog()
        assert fb1 is False
        first_calls = fetch.await_count
        models2, _ = await get_catalog()
        # served from cache on the second call
        assert fetch.await_count == first_calls
        assert models == models2
        # force refresh hits the API again
        await get_catalog(force=True)
        assert fetch.await_count == first_calls + 1
    mc._cache["models"] = None


@pytest.mark.asyncio
async def test_get_catalog_falls_back_when_fetch_fails():
    mc._cache["models"] = None
    with patch.object(mc, "fetch_catalog", AsyncMock(side_effect=httpx.ConnectError("boom"))):
        models, fallback = await get_catalog()
    assert fallback is True
    assert models[0].get("fallback") is True
    mc._cache["models"] = None


@pytest.mark.asyncio
async def test_resolve_council_models_family_diverse_and_honest_about_deficit():
    mc._cache["models"] = None
    catalog = _parse_catalog(CATALOG_BODY)
    with patch.object(mc, "get_catalog", AsyncMock(return_value=(catalog, False))):
        resolved = await resolve_council_models(count=4)
    ids = resolved["ids"]
    assert resolved["fallback"] is False
    # no paid models ever
    assert all(i not in ("openai/gpt-4o", "evil/billed:free") for i in ids)
    # one model per family (diversity)
    assert len(ids) == len({m["family"] for m in catalog if m["id"] in set(ids)})
    assert "deepseek/deepseek-chat-v3.1:free" in ids
    assert "google/gemma-3-27b-it:free" in ids
    assert "deficient" in resolved


@pytest.mark.asyncio
async def test_resolve_council_models_reports_honest_shortage():
    catalog = [{"id": "a/one:free", "name": "One", "family": "a", "context_length": None, "free": True}]
    with patch.object(mc, "get_catalog", AsyncMock(return_value=(catalog, False))):
        resolved = await resolve_council_models(count=4)
    assert len(resolved["ids"]) == 1
    assert resolved["deficient"] is True


def test_fallback_ids_are_free_variants():
    from backend.config import FALLBACK_FREE_MODEL_IDS

    assert FALLBACK_FREE_MODEL_IDS, "fallback list must exist and be documented"
    assert all(mid.endswith(":free") for mid in FALLBACK_FREE_MODEL_IDS)
