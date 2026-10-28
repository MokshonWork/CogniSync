"""Regression tests for the council lifecycle (progressive events),
storage persistence, retry dedupe, and free-mode model selection.

No real network calls — providers and catalogs are mocked.
"""

import json

import pytest

from backend import council
from backend.candidates import Candidate

def _candidate(model, text="answer", success=True, error=None, tokens=10, duration=5):
    c = Candidate(
        candidate_id=model,
        provider="openrouter",
        model=model,
        response=text if success else None,
        success=success,
        error=error,
        duration_ms=duration,
    )
    c.token_usage = {"total_tokens": tokens} if success else {}
    return c


class ModelKeyedProvider:
    """Fake provider returning scripted results per model id, not call order."""

    def __init__(self, by_model):
        self.by_model = by_model
        self.calls = []

    async def generate(self, model, messages, **kwargs):
        self.calls.append((model, messages))
        if model not in self.by_model:
            return _candidate(model, success=False, error="unexpected model", duration=1)
        return self.by_model[model]()


async def collect_events(gen):
    return [e async for e in gen]


def parse_sse(gen_events):
    """_run_council_events_stream yields SSE strings; decode them."""
    out = []
    for e in gen_events:
        assert e.startswith("data: ") and e.endswith("\n\n")
        out.append(json.loads(e[len("data: "):-2]))
    return out


# --- run_council_events ------------------------------------------------------


async def test_events_partial_success_streams_and_synthesizes():

    # Build a provider that behaves differently by phase:
    class Phased:
        name = "fake"

        def __init__(self):
            self.s1 = {"m/a": _candidate("m/a", "Answer A", tokens=50),
                       "m/b": _candidate("m/b", success=False, error="rate_limit: 429"),
                       "m/c": _candidate("m/c", "Answer C", tokens=60)}
            self.s2_results = {
                "m/a": _candidate("m/a", "A covers X; B misses Y.\n\nFINAL RANKING:\n1. Response A\n2. Response B"),
                "m/c": _candidate("m/c", "B is concise.\n\nFINAL RANKING:\n1. Response B\n2. Response A"),
            }
            self.chair = _candidate("m/a", "Final synthesized answer.")

        async def generate(self, model, messages, **kwargs):
            if "collective wisdom" in messages[-1]["content"]:
                return self.chair
            if "anonymized" in messages[-1]["content"]:
                return self.s2_results.get(model, _candidate(model, success=False, error="no review"))
            return self.s1.get(model, _candidate(model, success=False, error="no answer"))

    events = await collect_events(council.run_council_events(
        "q", models=["m/a", "m/b", "m/c"], chairman_model="m/a", provider=Phased(),
    ))
    types = [e["type"] for e in events]

    # stage1_start first, one stage1_result per model, reviews, rankings, synthesis at the end
    assert types[0] == "stage1_start"
    assert events[0]["models"] == ["m/a", "m/b", "m/c"]
    s1 = [e for e in events if e["type"] == "stage1_result"]
    assert len(s1) == 3
    a = next(e for e in s1 if e["model"] == "m/a")
    assert a["success"] and a["response"] == "Answer A" and a["tokens"] == 50
    b = next(e for e in s1 if e["model"] == "m/b")
    assert not b["success"] and "rate_limit" in b["error"]

    assert "run_failed" not in types
    reviews = [e for e in events if e["type"] == "stage2_result"]
    assert len(reviews) == 2
    assert all(e["success"] for e in reviews)
    assert all(e["parsed_ranking"] for e in reviews)

    rankings = next(e for e in events if e["type"] == "rankings")
    # A ranked first by reviewer A, second by C; B ranked first by C → tie; both recorded
    models_ranked = {r["model"] for r in rankings["aggregate"]}
    assert models_ranked == {"m/a", "m/c"}

    s3 = next(e for e in events if e["type"] == "stage3_result")
    assert s3["stage3"] == {"model": "m/a", "response": "Final synthesized answer.",
                            "success": True, "error": None}


async def test_events_all_stage1_failure_emits_run_failed():
    provider = ModelKeyedProvider({
        "m/a": _candidate("m/a", success=False, error="invalid_key: 401"),
        "m/b": _candidate("m/b", success=False, error="rate_limit: 429"),
    })

    class AllFail:
        name = "fake"

        async def generate(self, model, messages, **kwargs):
            return _candidate(model, success=False, error="unit-failure")

    events = await collect_events(council.run_council_events(
        "q", models=["m/a", "m/b"], chairman_model="m/a", provider=AllFail(),
    ))
    types = [e["type"] for e in events]
    assert "stage1_result" in types
    failed = next(e for e in events if e["type"] == "run_failed")
    assert "failed" in failed["error"]
    assert "stage2_start" not in types
    assert "stage3_result" not in types
    # No successful response fabricated
    agg_events = [e for e in events if e["type"] == "rankings"]
    assert agg_events == []


async def test_events_chairman_failure_falls_back_to_stage1_success():
    class ChairmanDies:
        name = "fake"

        def __init__(self):
            self.phase = 0

        async def generate(self, model, messages, **kwargs):
            if "Chairman" in messages[-1]["content"]:
                # first call (configured chairman) fails, second (fallback) succeeds
                self.phase += 1
                if self.phase == 1:
                    return _candidate("m/z", success=False, error="model unavailable")
                return _candidate("m/b", "Real synthesized answer.")
            return _candidate(model, f"Answer from {model}")

    events = await collect_events(council.run_council_events(
        "q", models=["m/a", "m/b"], chairman_model="m/z", provider=ChairmanDies(),
    ))
    s3 = next(e for e in events if e["type"] == "stage3_result")
    assert s3["stage3"]["success"] is True
    # fallback selects the first Stage-1 SUCCESS (arrival order) — never m/z
    assert s3["stage3"]["model"] in ("m/a", "m/b")
    assert "Real synthesized answer" in s3["stage3"]["response"]


# --- storage ----------------------------------------------------------------


def test_storage_title_and_dedupe_and_updated_at():
    from backend import storage

    conv = storage.create_conversation("t-conv-1")
    assert conv["title"] == "New Conversation"

    storage.add_user_message("t-conv-1", "What is a black hole?")
    storage.add_user_message("t-conv-1", "What is a black hole?")  # retry: must NOT duplicate

    conv = storage.get_conversation("t-conv-1")
    assert len(conv["messages"]) == 1
    assert conv["title"] == "What is a black hole?"
    assert conv["updated_at"]

    storage.add_assistant_message("t-conv-1", [], [], {"response": "ok", "success": True}, metadata={})
    storage.add_user_message("t-conv-1", "What is a black hole?")  # different turn context → new message
    conv = storage.get_conversation("t-conv-1")
    assert len(conv["messages"]) == 3

    listing = storage.list_conversations()
    entry = next(c for c in listing if c["id"] == "t-conv-1")
    assert entry["title"] == "What is a black hole?"
    assert entry["message_count"] == 3


# --- free-mode model validation ---------------------------------------------


async def test_run_council_events_dedupes_models(monkeypatch):
    ids = []

    class P:
        name = "fake"

        async def generate(self, model, messages, **kwargs):
            ids.append(model)
            return _candidate(model, success=False, error="stop here")

    events = await collect_events(council.run_council_events(
        "q", models=["m/a", "m/a", " m/a "], provider=P(),
    ))
    # deduped: exactly one model requested; " m/a " cleaned to the id key
    assert ids.count("m/a") == 1
    assert len(ids) == 1
    failed = next(e for e in events if e["type"] == "run_failed")
    assert failed["stage1"][0]["model"] == "m/a"


# --- conversation lifecycle via _run_council_for_request ---------------------


async def test_conversation_message_persists_user_and_result(tmp_path, monkeypatch):
    import uuid
    from unittest.mock import patch

    from backend import main, storage

    monkeypatch.setenv("FREE_TESTING_MODE", "true")
    cid = str(uuid.uuid4())
    storage.create_conversation(cid)

    catalog = [
        {"id": "m/a", "name": "Model A", "family": "a", "free": True, "context_length": 0},
        {"id": "m/b", "name": "Model B", "family": "b", "free": True, "context_length": 0},
    ]

    async def fake_catalog(force=False):
        return catalog, False

    async def fake_resolve(count):
        return {"ids": ["m/a", "m/b"], "names": {"m/a": "Model A", "m/b": "Model B"},
                "fallback": False, "deficient": False, "requested": count, "note": ""}

    class Sequential:
        async def generate(self, model, messages, **kwargs):
            content = messages[-1]["content"]
            if "collective wisdom" in content:
                return _candidate("m/b", "Chairman synthesis.")
            if "anonymized" in content:
                return _candidate(model, f"Review {model}.\n\nFINAL RANKING:\n1. Response B\n2. Response A")
            return _candidate(model, f"Answer from {model}")

    with patch("backend.model_catalog.get_catalog", fake_catalog), \
         patch("backend.model_catalog.resolve_council_models", fake_resolve), \
         patch("backend.council._build_provider", lambda: Sequential()):
        result = await main._process_and_run_council(cid, type("R", (), {
            "content": "Tell me about mars", "web_search": False, "attachments": [], "models": None,
        })())

    conv = storage.get_conversation(cid)
    roles = [m["role"] for m in conv["messages"]]
    assert roles == ["user", "assistant"]
    assert json.loads(conv["messages"][0]["content"])["content"] == "Tell me about mars"
    assistant = conv["messages"][1]
    assert len(assistant["stage1"]) == 2
    assert assistant["stage3"]["response"] == "Chairman synthesis."
    # Single final-answer contract is persisted AND returned by the API.
    assert assistant["final_answer"] == "Chairman synthesis."
    assert result["final_answer"] == "Chairman synthesis."
    assert result["final_answer_error"] is None
    assert result["metadata"]["aggregate_rankings"]
    assert conv["title"].startswith("Tell me about")


async def test_paid_model_ids_rejected_in_free_mode(tmp_path, monkeypatch):
    from backend import main
    from unittest.mock import patch

    catalog = [
        {"id": "m/free", "name": "Free", "family": "a", "free": True, "context_length": 0},
    ]

    async def fake_catalog(force=False):
        return catalog, False

    with patch("backend.model_catalog.get_catalog", fake_catalog), \
         patch("backend.config.FREE_TESTING_MODE", True):
        res = await main._resolve_request_models(["m/free"])
        assert res["ids"] == ["m/free"]

        with pytest.raises(main._PaidModelError):
            await main._resolve_request_models(["openai/gpt-4o"])  # not in free catalog


# --- api behavior on continuity ---------------------------------------------


async def test_events_stream_generator_persists_exactly_one_user_message(tmp_path, monkeypatch):
    import uuid
    from unittest.mock import patch

    from backend import main, storage
    from backend.main import _run_council_events_stream

    cid = str(uuid.uuid4())
    storage.create_conversation(cid)

    async def fake_resolve(chosen=None):
        return {"ids": ["m/a"], "names": {"m/a": "Model A"}, "fallback": False,
                "deficient": False, "requested": 2, "note": ""}

    async def fake_catalog(force=False):
        return [{"id": "m/a", "name": "Model A", "family": "a", "free": True, "context_length": 0}], False

    class OneGood:
        async def generate(self, model, messages, **kwargs):
            if "Chairman" in messages[-1]["content"]:
                return _candidate("m/a", "synth")
            if "evaluating different responses" in messages[-1]["content"]:
                return _candidate("m/a", "review\n\nFINAL RANKING:\n1. Response A")
            return _candidate("m/a", "answer")

    with patch("backend.main._resolve_request_models", fake_resolve), \
         patch("backend.model_catalog.get_catalog", fake_catalog), \
         patch("backend.council._build_provider", lambda: OneGood()):
        sse_strings = await collect_events(_run_council_events_stream(
            content="hello council", web_search=False,
            context_parts=[], request_models=None, user_extra=None,
            conversation_id=cid, user_id=None,
        ))
    events = parse_sse(sse_strings)

    types = [e["type"] for e in events]
    assert types[-1] == "done"
    final = next(e for e in events if e["type"] == "final")
    assert final["payload"]["stage3"]["response"] == "synth"
    conv = storage.get_conversation(cid)
    assert conv["messages"][0]["role"] == "user"
    assert json.loads(conv["messages"][0]["content"])["content"] == "hello council"
    assert len([m for m in conv["messages"] if m["role"] == "assistant"]) == 1


# --- stage-1 fallback policy --------------------------------------------------


def _c2(model, text="answer", success=True, error=None, category=None, tokens=10, duration=5):
    c = Candidate(candidate_id=model, provider="openrouter", model=model,
                  response=text if success else None, success=success, error=error,
                  error_category=category if not success else None,
                  duration_ms=duration)
    c.token_usage = {"total_tokens": tokens} if success else {}
    return c


async def test_fallback_transient_rate_limit_switches_models():
    on_primary = True

    class Fallbacking:
        name = "fake"

        async def generate(self, model, messages, **kw):
            nonlocal on_primary
            if model == "m/a":
                if on_primary:
                    on_primary = False
                    return _c2("m/a", success=False, error="rate_limit: 429 (provider X at capacity)",
                               category="rate_limit")
                return _c2(model, f"Answer via {model}")
            if model == "fallback/a":
                return _c2(model, "Answer from fallback")
            return _c2(model, success=False, error="gone", category="model_unavailable")

    events = await collect_events(council.run_council_events(
        "q", models=["m/a"], chairman_model="fallback/a", provider=Fallbacking(),
        alternatives={"m/a": ["fallback/a"]},
    ))
    s1 = next(e for e in events if e["type"] == "stage1_result")
    assert s1["success"] is True
    assert s1["fallback_used"] == "fallback/a"
    assert "rate_limit" in s1["error_original"]
    assert "Answer from fallback" in s1["response"]
    # synthesis succeeded on the fallback answer
    s3 = next(e for e in events if e["type"] == "stage3_result")
    assert s3["stage3"]["success"] is True


async def test_no_fallback_on_daily_quota_and_no_infinite_loop():
    calls = {"m/a": 0, "fallback/a": 0}

    class QuotaBlocked:
        name = "fake"

        async def generate(self, model, messages, **kw):
            calls[model] = calls.get(model, 0) + 1
            return _c2(model, success=False, error="OpenRouter free-tier daily request quota is exhausted",
                       category="quota")

    events = await collect_events(council.run_council_events(
        "q", models=["m/a"], chairman_model="m/a", provider=QuotaBlocked(),
        alternatives={"m/a": ["fallback/a"]},
    ))
    failed = next(e for e in events if e["type"] == "run_failed")
    assert "daily request quota" in failed["error"]
    # quota is account-wide: the alternative was never even tried
    assert calls["m/a"] == 1 and calls.get("fallback/a", 0) == 0


async def test_alternatives_dedupe_no_duplicate_members():
    gen = {"count": 0}

    class Everything500:
        name = "fake"

        async def generate(self, model, messages, **kw):
            gen["count"] += 1
            return _c2(model, success=False, error="model unavailable", category="model_unavailable")

    events = await collect_events(council.run_council_events(
        "q", models=["m/a", "m/b"], provider=Everything500(),
        alternatives={"m/a": ["m/b"], "m/b": ["m/b"]},  # m/b already a primary slot
    ))
    # "m/b" never used as m/a's substitute because it is a primary member
    stage1 = [e for e in events if e["type"] == "stage1_result"]
    fallbacks = [e.get("fallback_used") for e in stage1]
    assert None in fallbacks
    assert all(f is None for f in fallbacks), "no duplicate members via fallback"


async def test_quota_run_message_not_placeholder_only():
    class AllQuota:
        name = "fake"

        async def generate(self, model, messages, **kw):
            return _c2(model, success=False, error="quota exhausted", category="quota")

    events = await collect_events(council.run_council_events(
        "q", models=["m/a", "m/b"], provider=AllQuota(),
    ))
    failed = next(e for e in events if e["type"] == "run_failed")
    assert "quota" in failed["error"]
    assert "UTC midnight" in failed["error"]
    for e in failed["stage1"]:
        assert e["error_category"] == "quota"
