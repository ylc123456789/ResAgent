"""Conversation-layer models — independent from ResearchState by design.

See docs/CONVERSATION_LAYER_DESIGN.md §4.2.

The conversation layer is ResAgent's front desk: it routes, clarifies, and
presents. It never mutates ResearchState directly; the only gate into a
research run is ResearchBrief + explicit user confirmation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_conversation_id() -> str:
    today = _utcnow().strftime("%Y%m%d")
    return f"conv-{today}-{uuid.uuid4().hex[:6]}"


# ── Events ────────────────────────────────────────────────────────────────────

class ConversationEventType(str, Enum):
    user_message = "user_message"
    assistant_message = "assistant_message"
    tool_call = "tool_call"
    tool_result = "tool_result"
    brief_proposed = "brief_proposed"
    brief_confirmed = "brief_confirmed"
    brief_rejected = "brief_rejected"
    run_created = "run_created"
    run_advanced = "run_advanced"
    error = "error"


class ConversationEvent(BaseModel):
    """One append-only event in the conversation log (events.jsonl).

    Events whose payload contains a "state_patch" dict mutate the derived
    ConversationState; the patch is applied both on append and on rebuild.
    """
    seq: int
    type: ConversationEventType
    ts: datetime = Field(default_factory=_utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Artifact references surfaced during conversation ─────────────────────────

class ConvArtifactRef(BaseModel):
    """Lightweight artifact reference carried by the conversation.

    The `type` vocabulary matches ExpAgent's ArtifactRef so refs can be
    forwarded into AdvisorContext without translation.
    """
    id: str
    type: str = "other"  # repro_result | code_patch | run_log | metric_summary | other
    path: str = ""
    summary: str = ""
    source: str = ""     # "expagent" | "codingagent_qa" | "user" | run_id


# ── ResearchBrief: the ONLY gate into ResearchState ──────────────────────────

class ResearchBrief(BaseModel):
    """Distilled research proposal, presented to the user for confirmation.

    Nothing enters ResearchState until the user confirms a brief.
    """
    goal: str
    hypothesis: str = ""
    context_summary: str = ""
    constraints: list[str] = Field(default_factory=list)
    relevant_artifacts: list[ConvArtifactRef] = Field(default_factory=list)
    suggested_first_step: str = ""

    @field_validator("constraints", mode="before")
    @classmethod
    def _coerce_constraints(cls, v):
        """LLMs often pass constraints as one prose string; accept it."""
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, list):
            return [str(i) for i in v]
        return v

    @field_validator("relevant_artifacts", mode="before")
    @classmethod
    def _coerce_artifacts(cls, v):
        """Accept bare strings as artifact references (path/summary)."""
        if isinstance(v, list):
            out = []
            for i, item in enumerate(v):
                if isinstance(item, str):
                    out.append({"id": f"art_{i}", "path": item,
                                "summary": item[:80], "source": "user"})
                else:
                    out.append(item)
            return out
        return v

    def render_goal_text(self) -> str:
        """Compose the research_goal string passed to orchestrator.init_run()."""
        parts = [self.goal]
        if self.hypothesis:
            parts.append(f"Hypothesis: {self.hypothesis}")
        if self.context_summary:
            parts.append(f"Background: {self.context_summary}")
        if self.constraints:
            parts.append("Constraints: " + "; ".join(self.constraints))
        return "\n\n".join(parts)

    def render_display(self) -> str:
        """Human-readable rendering for confirmation prompts."""
        lines = [f"  Goal: {self.goal}"]
        if self.hypothesis:
            lines.append(f"  Hypothesis: {self.hypothesis}")
        if self.context_summary:
            lines.append(f"  Background: {self.context_summary}")
        if self.constraints:
            lines.append(f"  Constraints: {'; '.join(self.constraints)}")
        if self.suggested_first_step:
            lines.append(f"  Suggested first step: {self.suggested_first_step}")
        return "\n".join(lines)


# ── Sub-session index (docs/SESSION_AND_PROJECT_MODEL.md §3) ─────────────────

class SessionRef(BaseModel):
    """Index entry for a sub-agent session (its session.yaml card).

    The big conversation never holds sub-session content — only this index.
    The card file itself is owned and written by the sub-module.
    """
    module: str            # reproagent | codingagent | expagent
    session_id: str
    manifest_path: str     # absolute path to session.yaml
    status: str = ""
    summary: str = ""
    run_id: str = ""       # owning research run, if any


class ConversationState(BaseModel):
    """Derived view over the event log. Small, serializable, rebuildable."""
    conversation_id: str
    workspace_root: str
    active_run_id: str | None = None
    scratch_summary: str = ""  # compressed older conversation
    recent_artifacts: list[ConvArtifactRef] = Field(default_factory=list)  # cap 20
    session_index: list[SessionRef] = Field(default_factory=list)  # cap 50
    pending_brief: ResearchBrief | None = None
    event_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    MAX_RECENT_ARTIFACTS: ClassVar[int] = 20
    MAX_SESSION_INDEX: ClassVar[int] = 50

    def apply_patch(self, patch: dict[str, Any]) -> None:
        """Apply a state_patch from an event payload."""
        if not patch:
            return
        if "active_run_id" in patch:
            self.active_run_id = patch["active_run_id"]
        if "scratch_summary" in patch:
            self.scratch_summary = patch["scratch_summary"]
        if "add_artifacts" in patch:
            for a in patch["add_artifacts"]:
                self.recent_artifacts.append(ConvArtifactRef.model_validate(a))
            self.recent_artifacts = self.recent_artifacts[-self.MAX_RECENT_ARTIFACTS:]
        if "add_sessions" in patch:
            for s in patch["add_sessions"]:
                ref = SessionRef.model_validate(s)
                # upsert by (module, session_id): refresh status/summary/path
                self.session_index = [
                    r for r in self.session_index
                    if not (r.module == ref.module and r.session_id == ref.session_id)
                ]
                self.session_index.append(ref)
            self.session_index = self.session_index[-self.MAX_SESSION_INDEX:]
        if "pending_brief" in patch:
            pb = patch["pending_brief"]
            self.pending_brief = ResearchBrief.model_validate(pb) if pb else None


# ── ExpertCard: the expert "business card" (agent.yaml in memory) ────────────

class ExpertCard(BaseModel):
    """Self-description of an expert module. The only cross-module contract.

    Loaded from <module>/agent.yaml, config overrides, or built-in defaults.
    """
    name: str
    version: str = ""
    role: str = ""
    description_for_router: str = ""
    capabilities: list[str] = Field(default_factory=list)
    side_effects: Literal["none", "workspace", "workspace_and_environment"] = "none"
    requires_confirmation: bool | None = None  # None -> derive from side_effects
    cost_profile: dict[str, Any] = Field(default_factory=dict)
    input_contract: str = ""
    output_contract: str = ""
    status: Literal["available", "planned", "unavailable"] = "available"

    def model_post_init(self, __context: Any) -> None:
        if self.requires_confirmation is None:
            self.requires_confirmation = self.side_effects != "none"

    def router_line(self) -> str:
        """One-card summary injected into the chat system prompt."""
        caps = ", ".join(self.capabilities) if self.capabilities else "(none listed)"
        status_note = "" if self.status == "available" else f" [status: {self.status}]"
        return (
            f"- {self.name} (role: {self.role}, side_effects: {self.side_effects}, "
            f"capabilities: {caps}){status_note}\n"
            f"  {self.description_for_router.strip()}"
        )
