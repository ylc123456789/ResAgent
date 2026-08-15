"""Conversation persistence — append-only event log + atomic state snapshot.

See docs/reference/CONVERSATION_LAYER_DESIGN.md §4.3.

Layout:
    <workspace_root>/conversations/<conversation_id>/
        conversation.json   # ConversationState snapshot (atomic write)
        events.jsonl        # append-only ConversationEvent log (authoritative)
        experts/            # per-consult expert outputs
        briefs/             # archived research briefs

The event log is authoritative: the snapshot can always be rebuilt from it.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    ConversationEvent,
    ConversationEventType,
    ConversationState,
    new_conversation_id,
)


def conversations_root(workspace_root: str, dirname: str = "conversations") -> Path:
    return Path(workspace_root) / dirname


def conversation_dir(workspace_root: str, conversation_id: str,
                     dirname: str = "conversations") -> Path:
    return conversations_root(workspace_root, dirname) / conversation_id


def _snapshot_path(conv_dir: Path) -> Path:
    return conv_dir / "conversation.json"


def _events_path(conv_dir: Path) -> Path:
    return conv_dir / "events.jsonl"


# ── lifecycle ─────────────────────────────────────────────────────────────────

def new_conversation(workspace_root: str, dirname: str = "conversations") -> ConversationState:
    conv = ConversationState(
        conversation_id=new_conversation_id(),
        workspace_root=str(Path(workspace_root).resolve()),
    )
    conv_dir = conversation_dir(conv.workspace_root, conv.conversation_id, dirname)
    (conv_dir / "experts").mkdir(parents=True, exist_ok=True)
    (conv_dir / "briefs").mkdir(parents=True, exist_ok=True)
    save_conversation(conv, dirname)
    return conv


def save_conversation(conv: ConversationState, dirname: str = "conversations") -> None:
    """Atomically write the ConversationState snapshot."""
    conv_dir = conversation_dir(conv.workspace_root, conv.conversation_id, dirname)
    conv_dir.mkdir(parents=True, exist_ok=True)
    conv.updated_at = datetime.now(timezone.utc)

    payload = json.loads(conv.model_dump_json())
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", dir=conv_dir, delete=False, encoding="utf-8"
    ) as tf:
        json.dump(payload, tf, indent=2, ensure_ascii=False, default=str)
        tmp = tf.name
    os.replace(tmp, _snapshot_path(conv_dir))


def load_conversation(workspace_root: str, conversation_id: str,
                      dirname: str = "conversations") -> ConversationState | None:
    sp = _snapshot_path(conversation_dir(workspace_root, conversation_id, dirname))
    if not sp.exists():
        return None
    payload = json.loads(sp.read_text(encoding="utf-8"))
    return ConversationState.model_validate(payload)


def list_conversations(workspace_root: str, dirname: str = "conversations") -> list[str]:
    root = conversations_root(workspace_root, dirname)
    if not root.exists():
        return []
    return sorted(
        (d.name for d in root.iterdir()
         if d.is_dir() and _snapshot_path(d).exists()),
        reverse=True,
    )


# ── events ────────────────────────────────────────────────────────────────────

def append_event(
    conv: ConversationState,
    type: ConversationEventType,
    payload: dict,
    dirname: str = "conversations",
) -> ConversationEvent:
    """Append one event: apply its state_patch, bump count, persist both files."""
    conv.event_count += 1
    event = ConversationEvent(seq=conv.event_count, type=type, payload=payload)

    conv.apply_patch(payload.get("state_patch", {}))

    conv_dir = conversation_dir(conv.workspace_root, conv.conversation_id, dirname)
    conv_dir.mkdir(parents=True, exist_ok=True)
    with open(_events_path(conv_dir), "a", encoding="utf-8") as f:
        f.write(event.model_dump_json() + "\n")

    save_conversation(conv, dirname)
    return event


def read_events(workspace_root: str, conversation_id: str,
                dirname: str = "conversations") -> list[ConversationEvent]:
    ep = _events_path(conversation_dir(workspace_root, conversation_id, dirname))
    if not ep.exists():
        return []
    events = []
    for line in ep.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(ConversationEvent.model_validate_json(line))
    return events


def rebuild_from_events(workspace_root: str, conversation_id: str,
                        dirname: str = "conversations") -> ConversationState | None:
    """Rebuild ConversationState purely from the event log."""
    events = read_events(workspace_root, conversation_id, dirname)
    if not events:
        return None

    conv = ConversationState(
        conversation_id=conversation_id,
        workspace_root=str(Path(workspace_root).resolve()),
        created_at=events[0].ts,
        updated_at=events[-1].ts,
    )
    for event in events:
        conv.event_count = event.seq
        conv.apply_patch(event.payload.get("state_patch", {}))
    return conv
