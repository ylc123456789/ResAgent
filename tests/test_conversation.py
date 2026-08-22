"""Tests for conversation persistence (event log + snapshot)."""

from resagent.conversation.models import ConversationEventType, ConversationState
from resagent.conversation import (
    append_event,
    load_conversation,
    new_conversation,
    read_events,
)


def test_save_load_roundtrip(tmp_path):
    conv = new_conversation(str(tmp_path))
    loaded = load_conversation(str(tmp_path), conv.conversation_id)
    assert loaded is not None
    assert loaded.conversation_id == conv.conversation_id
    assert loaded.workspace_root == conv.workspace_root


def test_append_and_read_events(tmp_path):
    conv = new_conversation(str(tmp_path))
    append_event(conv, ConversationEventType.user_message, {"text": "hello"})
    append_event(conv, ConversationEventType.assistant_message, {"text": "hi"})

    events = read_events(str(tmp_path), conv.conversation_id)
    assert len(events) == 2
    assert [e.seq for e in events] == [1, 2]
    assert events[0].type == ConversationEventType.user_message
    assert conv.event_count == 2


def test_recent_artifacts_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(ConversationState, "MAX_RECENT_ARTIFACTS", 3)
    conv = new_conversation(str(tmp_path))
    for i in range(5):
        conv.apply_patch({"add_artifacts": [{"id": f"a{i}", "source": "expagent"}]})
    assert len(conv.recent_artifacts) == 3
    assert conv.recent_artifacts[-1].id == "a4"
