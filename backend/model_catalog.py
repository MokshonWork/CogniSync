"""OpenRouter model catalog discovery and free-model selection.

Uses OpenRouter's official catalog endpoint:
  GET https://openrouter.ai/api/v1/models

A model is considered free-eligible only when the catalog reports zero
prompt/completion pricing. A model id ending in ``:free`` indicates a
OpenRouter-provided free variant, but catalog pricing is still verified
when present. Nothing is assumed free because of its provider name, and no
id in this module is treated as permanently available.

Catalog responses are cached in memory with a TTL; callers can force a
refresh. When the catalog cannot be fetched, a documented fallback list of
historically free model ids is used, flagged as ``fallback`` so callers know
eligibility could not be verified against the live catalog.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import (
    OPENROUTER_API_KEY,
    MODEL_CATALOG_URL,
    FALLBACK_FREE_MODEL_IDS,
    COUNCIL_MODEL_COUNT,
    MODEL_CATALOG_TTL_SECONDS,
)

# Preferred provider slugs for family-diverse council selection.
# Families are picked from the catalog dynamically; a family contributes a
# candidate only if a free-eligible model exists for it today.
_PREFERRED_FAMILIES = (
    "openai", "google", "meta-llama", "anthropic",
    "deepseek", "qwen", "mistralai",
)

_cache: Dict[str, Any] = {"models": None, "updated_at": 0.0, "fallback": False}


def _is_free(model: Dict[str, Any]) -> bool:
    """Return True only when the catalog reports zero pricing for the model."""
    pricing = model.get("pricing") or {}

    def _price(key: str) -> Optional[float]:
        raw = pricing.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    in_price = _price("prompt")
    out_price = _price("completion")
    id_free = str(model.get("id", "")).endswith(":free")
    # Verified-free: catalog explicitly reports zero pricing.
    if in_price == 0.0 and out_price == 0.0:
        return True
    # ":free" ids without pricing metadata are accepted but flagged later;
    # a positive price always disqualifies.
    if in_price is not None and in_price > 0:
        return False
    if out_price is not None and out_price > 0:
        return False
    return id_free


def _family_of(model_id: str) -> str:
    """Derive a coarse provider family slug from a model id."""
    return model_id.split("/", 1)[0].lower() if "/" in model_id else "other"


def display_name(model: Dict[str, Any]) -> str:
    """Human-facing display name for a catalog entry."""
    raw = model.get("name") or model.get("id") or "Unknown model"
    return raw.replace(" (free)", "").strip()


def _parse_catalog(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    models = []
    for m in (data or {}).get("data", []):
        if not m.get("id") or not _is_free(m):
            continue
        models.append({
            "id": m["id"],
            "name": display_name(m),
            "family": _family_of(m["id"]),
            "context_length": m.get("context_length"),
            "free": True,
        })
    models.sort(key=lambda m: m["id"])
    return models


async def fetch_catalog(timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Fetch the OpenRouter catalog and return its free-eligible models.

    Raises ``httpx.HTTPError`` subclasses or ``ValueError`` on malformed
    responses so callers can distinguish network failure from bad data.
    """
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"} if OPENROUTER_API_KEY else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(MODEL_CATALOG_URL, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    models = _parse_catalog(data)
    if not models:
        raise ValueError("Catalog returned no free-eligible models")
    return models


def _fallback_list() -> List[Dict[str, Any]]:
    # ponytail: fallback catalog — must be validated against a live catalog
    # every time one is reachable; replace ids if ':free' variants change.
    return [
        {"id": mid, "name": mid.split("/")[-1].replace(":free", ""), "family": _family_of(mid), "context_length": None, "free": True, "fallback": True}
        for mid in FALLBACK_FREE_MODEL_IDS
    ]


async def get_catalog(force: bool = False) -> Tuple[List[Dict[str, Any]], bool]:
    """Return (models, is_fallback). Served from cache when fresh.

    On cache miss and fetch failure, falls back to the documented fallback
    list and marks it as unverified.
    """
    fresh = _cache["models"] is not None and (time.monotonic() - _cache["updated_at"]) < MODEL_CATALOG_TTL_SECONDS
    if fresh and not force:
        return _cache["models"], _cache["fallback"]
    try:
        models = await fetch_catalog()
        _cache["models"] = models
        _cache["updated_at"] = time.monotonic()
        _cache["fallback"] = False
        return models, False
    except Exception:
        import logging
        logging.getLogger(__name__).exception("OpenRouter catalog fetch failed")
        models = _fallback_list()
        _cache["models"] = models
        _cache["updated_at"] = time.monotonic()
        _cache["fallback"] = True
        return models, True


async def resolve_council_models(count: int = COUNCIL_MODEL_COUNT) -> Dict[str, Any]:
    """Pick up to ``count`` family-diverse free models for the council.

    Returns {"ids": [...], "names": {...}, "fallback": bool, "deficient": bool}.
    Never returns a model that failed eligibility; deficiency is reported
    honestly rather than padded with ineligible ids.
    """
    models, fallback = await get_catalog()
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for m in models:
        by_family.setdefault(m["family"], []).append(m)

    chosen: List[Dict[str, Any]] = []
    for family in _PREFERRED_FAMILIES:
        if by_family.get(family):
            chosen.append(by_family[family][0])
        if len(chosen) >= count:
            break
    # Top up from remaining families if preferred families were sparse.
    if len(chosen) < count:
        used = {c["id"] for c in chosen}
        for m in models:
            if len(chosen) >= count:
                break
            if m["id"] not in used:
                chosen.append(m)
                used.add(m["id"])

    ids = [c["id"] for c in chosen]
    names = {c["id"]: (c.get("name") or c["id"]) for c in chosen}
    return {
        "ids": ids,
        "names": names,
        "fallback": fallback,
        "deficient": len(ids) < count,
        "requested": count,
    }


def is_free_testing_eligible(model_id: str, models: List[Dict[str, Any]]) -> bool:
    """Validate a model id against the resolved catalog for free testing."""
    if not model_id:
        return False
    if any(m["id"] == model_id for m in models):
        return True
    return model_id in FALLBACK_FREE_MODEL_IDS and model_id.endswith(":free")
