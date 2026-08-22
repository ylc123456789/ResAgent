"""State persistence — atomic reads/writes of ResearchState to state.json."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..models import (
    DirectiveKind, ResearchState, ResearchRun, RunStatus, TaskStatus,
    UserDirective,
)


_CONFIRMATIONS = {
    "accepted", "approve", "approved", "continue", "go ahead", "ok", "okay",
    "proceed", "yes", "y", "可以", "同意", "好", "好的", "确认", "继续",
}
_CONFIRMATION_PHRASE = re.compile(
    r"^(?:yes|y|ok(?:ay)?|approve(?:d)?|accepted|continue|proceed|go ahead)"
    r"(?:[\s,]+(?:please|now|continue|proceed|go ahead))*$",
    re.IGNORECASE,
)
_FINISH_CONTROL = re.compile(
    r"\b(finish|finalize|wrap\s*up|stop)\b|"
    r"收口|收尾|结束|停止|不要再?(?:继续|运行|跑)|不再(?:继续|运行|跑)",
    re.IGNORECASE,
)
_PLAN_REVISION = re.compile(
    r"\b(change|revise|replace|instead|only\s+run|single\s+seed)\b|"
    r"改成|改为|修改|调整|替换|换成|只跑|单\s*seed|增加|减少|重新规划|跳过",
    re.IGNORECASE,
)


def workspace_path(workspace_dir: str, run_id: str) -> Path:
    """Full path to a run workspace."""
    return Path(workspace_dir) / run_id


def state_path(workspace_dir: str, run_id: str) -> Path:
    """Path to state.json inside a run workspace."""
    return workspace_path(workspace_dir, run_id) / "state.json"


def save_state(state: ResearchState) -> None:
    """Atomically write ResearchState to its workspace."""
    ws = Path(state.run.workspace_dir) / state.run.run_id
    ws.mkdir(parents=True, exist_ok=True)

    state.run.updated_at = datetime.now(timezone.utc)
    sp = ws / "state.json"

    payload = _serialize(state)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=ws, delete=False, encoding="utf-8") as tf:
        json.dump(payload, tf, indent=2, ensure_ascii=False, default=str)
        tmp = tf.name
    os.replace(tmp, sp)


def load_state(workspace_dir: str, run_id: str) -> ResearchState | None:
    """Load ResearchState from a workspace. Returns None if not found."""
    sp = state_path(workspace_dir, run_id)
    if not sp.exists():
        return None
    payload = json.loads(sp.read_text(encoding="utf-8"))
    return _deserialize(payload)


def init_state(
    run_id: str,
    workspace_dir: str,
    research_goal: str,
) -> ResearchState:
    """Create a fresh ResearchState (does not write to disk). Call save_state() to persist."""
    run = ResearchRun(
        run_id=run_id,
        workspace_dir=str(workspace_dir),
        research_goal=research_goal,
    )
    return ResearchState(run=run)


# ── internal serialize helpers ────────────────────────────────────────────────

def _serialize(state: ResearchState) -> dict:
    return json.loads(state.model_dump_json())


def _deserialize(payload: dict) -> ResearchState:
    # States written before directive typing stored only free-form text. Migrate
    # those records through the same conservative classifier used for new input.
    for item in payload.get("user_directives", []):
        if "kind" in item:
            continue
        kind, command = classify_user_directive(str(item.get("text", "")))
        item["kind"] = kind.value
        item["command"] = command
        if kind in {DirectiveKind.information, DirectiveKind.confirmation}:
            item["handled"] = True
    return ResearchState.model_validate(payload)


def classify_user_directive(text: str) -> tuple[DirectiveKind, str]:
    """Classify only high-confidence orchestration effects.

    Unknown text is information, not an implicit plan revision. Explicit
    revision vocabulary still reaches ExpAgent, while finish/stop is handled
    deterministically by ResAgent.
    """
    normalized = " ".join(text.strip().lower().split()).strip("。.!！?？")
    if normalized in _CONFIRMATIONS or _CONFIRMATION_PHRASE.fullmatch(normalized):
        return DirectiveKind.confirmation, ""
    if _PLAN_REVISION.search(normalized):
        return DirectiveKind.plan_revision, ""
    if _FINISH_CONTROL.search(normalized):
        return DirectiveKind.control, "finish"
    return DirectiveKind.information, ""


def append_user_directive(
    state: ResearchState,
    text: str,
    *,
    source: str = "",
) -> UserDirective:
    """Append one classified, auditable user directive."""
    kind, command = classify_user_directive(text)
    directive = UserDirective(
        text=text.strip(),
        kind=kind,
        command=command,
        source_conversation=source,
        handled=kind in {DirectiveKind.information, DirectiveKind.confirmation},
    )
    state.user_directives.append(directive)
    return directive


def submit_user_response(state: ResearchState, question_id: str, response: str) -> None:
    """Record a response in-memory to the currently pending question and resume the run."""
    question = state.pending_question
    if question is None or question.question_id != question_id:
        raise ValueError(f"No pending question with id: {question_id}")
    answer = response.strip()
    if not answer:
        raise ValueError("User response must not be empty.")
    question.response = answer
    question.answered_at = datetime.now(timezone.utc)
    if question.task_id:
        task = state.find_task(question.task_id)
        if task is not None and task.status == TaskStatus.needs_user_input:
            task.status = TaskStatus.completed
            task.error = ""
    state.answered_questions.append(question)
    state.pending_question = None
    # Keep every answer visible to the controller, but preserve its effect:
    # information/confirmation is context, plan_revision invokes ExpAgent, and
    # finish/stop is a deterministic ResAgent control.
    append_user_directive(state, answer, source=f"answer:{question_id}")
    state.run.status = RunStatus.running
