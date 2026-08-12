"""State persistence — atomic reads/writes of ResearchState to state.json."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import ResearchState, ResearchRun, RunStatus, TaskStatus


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
    return ResearchState.model_validate(payload)


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
    state.run.status = RunStatus.running
