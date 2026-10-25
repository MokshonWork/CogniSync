"""Tests for the Candidate and CouncilResult result structures."""

from backend.candidates import Candidate, CouncilResult


def test_candidate_to_dict_roundtrip():
    c = Candidate(
        candidate_id="c1",
        provider="openrouter",
        model="model-a",
        response="hello",
        duration_ms=42,
        token_usage={"total_tokens": 5},
        success=True,
    )
    d = c.to_dict()
    assert d == {
        "candidate_id": "c1",
        "provider": "openrouter",
        "model": "model-a",
        "response": "hello",
        "duration_ms": 42,
        "token_usage": {"total_tokens": 5},
        "success": True,
        "error": None,
        "error_category": None,
    }


def test_candidate_failure_to_dict():
    c = Candidate(
        candidate_id="c2",
        provider="openrouter",
        model="model-b",
        success=False,
        duration_ms=100,
        error="Provider request failed",
    )
    d = c.to_dict()
    assert d["success"] is False
    assert d["response"] is None
    assert d["error"] == "Provider request failed"


def test_council_result_properties():
    good = Candidate(candidate_id="g", provider="openrouter", model="m1", response="ok", success=True)
    bad = Candidate(candidate_id="b", provider="openrouter", model="m2", success=False, error="fail")
    cr = CouncilResult(candidates=[good, bad], total_duration_ms=200)

    assert cr.success_count == 1
    assert cr.failure_count == 1
    assert len(cr.successful) == 1
    assert len(cr.failed) == 1
    assert cr.total_duration_ms == 200


def test_council_result_to_dict():
    c = Candidate(candidate_id="c1", provider="openrouter", model="m1", response="ok", success=True)
    cr = CouncilResult(candidates=[c])
    d = cr.to_dict()
    assert d["success_count"] == 1
    assert d["failure_count"] == 0
    assert len(d["candidates"]) == 1
    assert d["candidates"][0]["success"] is True
