"""History/persistence acceptance tests (no network).

Covers collapsed-list prerequisites (filtering empties), title derivation,
newest-updated-first ordering, timestamp bumps on follow-ups, retry dedupe,
reload from disk, and the persisted final-answer contract.
"""

import json
import os

import pytest

from backend import storage


@pytest.fixture(autouse=True)
def temp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    return tmp_path


def _add_turn(conv_id, question, answer="Answer", success=True, final_answer=None):
    storage.add_user_message(conv_id, json.dumps({"role": "user", "content": question}))
    storage.add_assistant_message(
        conv_id,
        [{"model": "m/a", "success": True, "response": "model answer"}],
        [{"model": "m/a", "success": True, "ranking": "FINAL RANKING:\n1. Response A"}],
        {"model": "m/a", "response": answer, "success": success, "error": None if success else "boom"},
        metadata={"label_to_model": {}, "aggregate_rankings": []},
        final_answer=final_answer if final_answer is not None else (answer if success else None),
        final_answer_error=None if success else "boom",
    )


# --- titles -----------------------------------------------------------------


def test_title_derives_from_first_meaningful_user_message():
    storage.create_conversation("c1")
    storage.add_user_message("c1", json.dumps({"content": "  How do   black\nholes work?  "}))
    conv = storage.get_conversation("c1")
    assert conv["title"] == "How do black holes work?"
    listing = storage.list_conversations()
    assert listing[0]["title"] == "How do black holes work?"


def test_title_truncates_long_prompts():
    storage.create_conversation("c1")
    storage.add_user_message("c1", "x" * 300)
    title = storage.get_conversation("c1")["title"]
    assert len(title) <= storage.TITLE_MAX_LENGTH
    assert title.endswith("…")


def test_legacy_new_conversation_title_is_derived_on_read(tmp_path):
    # Simulate an old saved record with a placeholder title but a real prompt.
    legacy = {
        "id": "legacy",
        "created_at": "2025-01-01T00:00:00",
        "updated_at": "2025-01-01T00:00:00",
        "title": "New Conversation",
        "messages": [{"role": "user", "content": "Explain RAG in one paragraph"}],
    }
    (tmp_path / "legacy.json").write_text(json.dumps(legacy))
    assert storage.get_conversation("legacy")["title"] == "Explain RAG in one paragraph"
    assert storage.list_conversations()[0]["title"] == "Explain RAG in one paragraph"


def test_empty_sessions_are_not_history_records():
    storage.create_conversation("empty-1")
    storage.create_conversation("empty-2")
    assert storage.list_conversations() == []
    assert {c["id"] for c in storage.list_conversations(include_empty=True)} == {"empty-1", "empty-2"}

    _add_turn("empty-1", "Real question")
    # Only the conversation that produced a user message is listed.
    assert [c["id"] for c in storage.list_conversations()] == ["empty-1"]
    assert storage.list_conversations()[0]["message_count"] == 2


# --- ordering / timestamps --------------------------------------------------


def test_newest_updated_conversation_first_and_follow_up_bumps():
    storage.create_conversation("older")
    _add_turn("older", "Older question")
    storage.create_conversation("newer")
    _add_turn("newer", "Newer question")

    assert [c["id"] for c in storage.list_conversations()] == ["newer", "older"]

    # A follow-up in the older conversation is the newest activity.
    _add_turn("older", "Follow-up question")
    listing = storage.list_conversations()
    assert listing[0]["id"] == "older"
    assert listing[0]["message_count"] == 4
    assert listing[0]["updated_at"] > listing[1]["updated_at"]
    assert listing[0]["created_at"] < listing[0]["updated_at"]


def test_equal_timestamps_sort_deterministically(monkeypatch):
    monkeypatch.setattr(storage, "_now_iso", lambda: "2026-01-01T00:00:00Z")
    for cid in ("a", "b", "c"):
        storage.create_conversation(cid)
        _add_turn(cid, f"question {cid}")
    first = [c["id"] for c in storage.list_conversations()]
    second = [c["id"] for c in storage.list_conversations()]
    assert first == second
    assert set(first) == {"a", "b", "c"}


def test_updated_at_is_iso_utc_with_zone():
    storage.create_conversation("c1")
    _add_turn("c1", "Question")
    updated = storage.list_conversations()[0]["updated_at"]
    assert updated.endswith("Z")


# --- persistence / retries --------------------------------------------------


def test_conversation_reloads_from_disk_after_restart():
    storage.create_conversation("persisted")
    _add_turn("persisted", "Survives restarts", final_answer="Yes it does")

    # Nothing is cached in-process: every read comes from the JSON file.
    reloaded = storage.get_conversation("persisted")
    assert reloaded["messages"][0]["role"] == "user"
    assert reloaded["messages"][-1]["final_answer"] == "Yes it does"
    assert [c["id"] for c in storage.list_conversations()] == ["persisted"]


def test_retry_does_not_duplicate_user_message_before_result():
    storage.create_conversation("retry")
    prompt = json.dumps({"content": "Same prompt"})
    storage.add_user_message("retry", prompt)
    storage.add_user_message("retry", prompt)  # simulated retry/rerender
    conv = storage.get_conversation("retry")
    assert [m["role"] for m in conv["messages"]] == ["user"]


def test_retry_after_failed_assistant_replaces_the_failed_turn():
    storage.create_conversation("retry")
    prompt = json.dumps({"content": "Same prompt"})
    storage.add_user_message("retry", prompt)
    storage.add_assistant_message("retry", [], [], {"model": "error", "response": "failed", "success": False},
                                  final_answer=None, final_answer_error="failed")
    # Retry with the identical prompt: the failed assistant entry is replaced,
    # not appended after a duplicate user message.
    storage.add_user_message("retry", prompt)
    storage.add_assistant_message("retry", [], [], {"model": "m/a", "response": "ok", "success": True},
                                  final_answer="ok")
    conv = storage.get_conversation("retry")
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][-1]["final_answer"] == "ok"
    assert conv["messages"][-1]["stage3"]["success"] is True


def test_final_answer_and_sources_are_persisted():
    storage.create_conversation("c1")
    storage.add_user_message("c1", "Q")
    storage.add_assistant_message(
        "c1", [], [], {"model": "m/a", "response": "Final text", "success": True},
        metadata={"search": {"enabled": True, "status": "success"}},
        final_answer="Final text",
        sources=[{"result_id": "r1", "title": "T", "url": "https://a.example", "domain": "a.example"}],
    )
    conv = storage.get_conversation("c1")
    assistant = conv["messages"][-1]
    assert assistant["final_answer"] == "Final text"
    assert assistant["sources"][0]["url"] == "https://a.example"


def test_legacy_messages_without_final_answer_still_load():
    legacy = {
        "id": "old",
        "created_at": "2025-01-01T00:00:00",
        "title": "Old",
        "messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "stage1": [], "stage2": [], "stage3": {"model": "m", "response": "legacy answer", "success": True}},
        ],
    }
    os.makedirs(storage.DATA_DIR, exist_ok=True)
    with open(storage.get_conversation_path("old"), "w") as f:
        json.dump(legacy, f)
    conv = storage.get_conversation("old")
    assert conv["messages"][-1]["stage3"]["response"] == "legacy answer"
    assert conv["messages"][-1].get("final_answer") is None  # frontend falls back to stage3
