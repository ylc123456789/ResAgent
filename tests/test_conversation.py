"""Tests for conversation persistence (event log + snapshot + rebuild)."""

import os

from resagent.chat_models import ConversationEventType, ConversationState
from resagent.conversation import (
    append_event,
    conversation_dir,
    list_conversations,
    load_conversation,
    new_conversation,
    read_events,
    rebuild_from_events,
)


def test_save_load_roundtrip(tmp_path):
    conv = new_conversation(str(tmp_path))
    loaded = load_conversation(str(tmp_path), conv.conversation_id)
    assert loaded is not None
    assert loaded.conversation_id == conv.conversation_id
    assert loaded.workspace_root == conv.workspace_root
    assert list_conversations(str(tmp_path)) == [conv.conversation_id]


def test_append_and_read_events(tmp_path):
    conv = new_conversation(str(tmp_path))
    append_event(conv, ConversationEventType.user_message, {"text": "hello"})
    append_event(conv, ConversationEventType.assistant_message, {"text": "hi"})

    events = read_events(str(tmp_path), conv.conversation_id)
    assert len(events) == 2
    assert [e.seq for e in events] == [1, 2]
    assert events[0].type == ConversationEventType.user_message
    assert conv.event_count == 2


def test_event_log_rebuild(tmp_path):
    """Snapshot deleted -> state fully rebuilt from events.jsonl."""
    conv = new_conversation(str(tmp_path))
    append_event(conv, ConversationEventType.user_message, {"text": "start something"})
    append_event(conv, ConversationEventType.tool_result, {
        "tool": "inspect_run",
        "ok": True,
        "detail": "...",
        "state_patch": {"active_run_id": "res-20260808-x1"},
    })
    append_event(conv, ConversationEventType.brief_proposed, {
        "brief": {"goal": "Verify X"},
        "state_patch": {"pending_brief": {"goal": "Verify X"}},
    })
    append_event(conv, ConversationEventType.tool_result, {
        "tool": "consult_expert",
        "ok": True,
        "detail": "advisory",
        "state_patch": {"add_artifacts": [
            {"id": "exp_consult_001", "summary": "s", "source": "expagent"},
        ]},
    })

    # Delete the snapshot; rebuild from events only.
    conv_dir = conversation_dir(str(tmp_path), conv.conversation_id)
    os.remove(conv_dir / "conversation.json")

    rebuilt = rebuild_from_events(str(tmp_path), conv.conversation_id)
    assert rebuilt is not None
    assert rebuilt.active_run_id == "res-20260808-x1"
    assert rebuilt.pending_brief is not None
    assert rebuilt.pending_brief.goal == "Verify X"
    assert rebuilt.recent_artifacts[0].id == "exp_consult_001"
    assert rebuilt.event_count == 4


def test_rebuild_empty_returns_none(tmp_path):
    assert rebuild_from_events(str(tmp_path), "conv-nonexistent") is None


def test_recent_artifacts_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(ConversationState, "MAX_RECENT_ARTIFACTS", 3)
    conv = new_conversation(str(tmp_path))
    for i in range(5):
        conv.apply_patch({"add_artifacts": [{"id": f"a{i}", "source": "expagent"}]})
    assert len(conv.recent_artifacts) == 3
    assert conv.recent_artifacts[-1].id == "a4"
