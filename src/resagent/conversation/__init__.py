"""Conversation models, durable history, tools, and agentic loop.

Import concrete symbols from the responsibility module that owns them. Keeping
package initialization lightweight avoids a capabilities/loop import cycle.
"""

from .history import (
    append_event,
    conversation_dir,
    conversations_root,
    list_conversations,
    load_conversation,
    new_conversation,
    read_events,
    rebuild_from_events,
    save_conversation,
)

__all__ = [
    "append_event", "conversation_dir", "conversations_root", "list_conversations",
    "load_conversation", "new_conversation", "read_events", "rebuild_from_events",
    "save_conversation",
]
