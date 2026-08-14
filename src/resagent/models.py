"""Core data models for ResAgent — all Pydantic, no framework lock-in."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class RunStatus(str, Enum):
    """Lifecycle status of a research run."""
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class TaskStatus(str, Enum):
    """Lifecycle status of a single task."""
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"
    needs_user_input = "needs_user_input"


class TaskPriority(str, Enum):
    """Priority level of a task."""
    high = "high"
    medium = "medium"
    low = "low"


class ArtifactType(str, Enum):
    """Type/category of an artifact."""
    scientific_decision = "scientific_decision"
    experiment_plan = "experiment_plan"
    code_patch = "code_patch"
    repro_result = "repro_result"
    log = "log"
    report = "report"
    other = "other"


class Producer(str, Enum):
    """Module that produced a task or artifact."""
    ExpAgent = "ExpAgent"
    CodingAgent = "CodingAgent"
    ReproAgent = "ReproAgent"
    ResAgent = "ResAgent"


class AgentKind(str, Enum):
    """Kind of work a task represents."""
    advise = "advise"
    coding_task = "coding_task"
    repro_task = "repro_task"
    ask_user = "ask_user"
    classify_failure = "classify_failure"


# ── Action space for the agentic loop ─────────────────────────────────────────

class ActionName(str, Enum):
    """Action the controller planner may select."""
    call_exp_agent = "call_exp_agent"
    call_coding_agent = "call_coding_agent"
    call_repro_agent = "call_repro_agent"
    classify_failure = "classify_failure"
    ask_user = "ask_user"
    finish = "finish"


# ── Core models ──────────────────────────────────────────────────────────────

class Budget(BaseModel):
    """Runtime resource budget for the current research run."""
    max_tasks: int = Field(default=20, description="Hard cap on total tasks")
    max_task_retries: int = Field(default=2, description="Max retries per task")
    max_api_calls: int = Field(default=200, description="Max total LLM calls")
    api_calls_used: int = Field(default=0)
    tasks_run: int = Field(default=0)


class ResearchRun(BaseModel):
    """Top-level identifier for one research project run."""
    run_id: str
    workspace_dir: str
    research_goal: str
    status: RunStatus = RunStatus.running
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Artifact(BaseModel):
    """Immutable record of any output produced by any module."""
    id: str
    type: ArtifactType
    producer: Producer
    path: str
    summary: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceRef(BaseModel):
    """A physical resource registered by an executor within this run."""

    kind: str
    id: str
    path: str = ""
    origin: str = ""
    repo: str = ""
    certification: str = ""
    created_by: Producer
    created_task: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Attempt(BaseModel):
    """Record of one execution attempt of a task."""
    attempt_number: int
    started_at: datetime
    finished_at: datetime | None = None
    error: str = ""
    artifacts: list[str] = Field(default_factory=list)  # artifact ids


class AgentTask(BaseModel):
    """One unit of work assigned to a module."""
    id: str
    source: str = ""  # id of artifact/decision that spawned this task
    agent: Producer
    kind: AgentKind
    status: TaskStatus = TaskStatus.pending
    priority: TaskPriority = TaskPriority.medium
    capability: str = ""
    required: bool = True
    fingerprint: str = ""
    action_id: str = ""
    depends_on: list[str] = Field(default_factory=list)  # ResAgent task ids
    project_ref: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)  # artifact ids
    attempts: list[Attempt] = Field(default_factory=list)
    error: str = ""
    warnings: list[str] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    """Why a particular orchestration decision was made."""
    id: str
    made_by: str  # "ResAgent" or "ExpAgent"
    reason: str
    selected_action: str
    alternatives: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # artifact/task ids
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Observation(BaseModel):
    """One observation recorded during the agentic loop."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: ActionName | None = None
    result: str = ""  # "ok" | "error" | "user_response_required" | "terminal" | "rejected"
    detail: str = ""
    artifact_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)


class UserDirective(BaseModel):
    """An explicit instruction from the user, injected via the chat layer.

    Directives persist as part of run history (auditable); the planner sees
    the most recent ones every step. See docs/CONVERSATION_LAYER_DESIGN.md §4.7.
    """
    text: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_conversation: str = ""


class PendingQuestion(BaseModel):
    """A question that requires user input before the run can continue."""
    question_id: str
    text: str
    task_id: str | None = None
    requested_fields: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    response: str | None = None
    answered_at: datetime | None = None


class ResearchState(BaseModel):
    """Full persistent state of a research run."""
    run: ResearchRun
    analysis_required: bool = Field(
        default=True,
        description=(
            "Whether terminal experiments must be covered by a completed "
            "analyze_results task before the run can finish. Set False only "
            "for pure engineering smoke tests (see §6.3 of the redesign doc)."
        ),
    )
    current_summary: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    resources: list[ResourceRef] = Field(default_factory=list)
    tasks: list[AgentTask] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    user_directives: list[UserDirective] = Field(default_factory=list)
    pending_question: PendingQuestion | None = None
    answered_questions: list[PendingQuestion] = Field(default_factory=list)

    # ── helpers ───────────────────────────────────────────────────────────

    def find_task(self, task_id: str) -> AgentTask | None:
        """Return the task with the given id, or None."""
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def find_task_by_fingerprint(self, fingerprint: str) -> AgentTask | None:
        """Return an existing semantically equivalent task, if any."""
        if not fingerprint:
            return None
        for task in self.tasks:
            if task.fingerprint == fingerprint:
                return task
        return None

    def find_artifact(self, artifact_id: str) -> Artifact | None:
        """Return the artifact with the given id, or None."""
        for a in self.artifacts:
            if a.id == artifact_id:
                return a
        return None

    def register_artifact(self, artifact: Artifact, task: "AgentTask | None" = None) -> None:
        """Register ``artifact`` as the CURRENT one under its id.

        Task-output artifact ids are task-derived (``repro_result_<n>``,
        ``code_patch_<n>``), so a retry re-registers the same id. The stale
        entry must be replaced: ``find_artifact`` returns the first match,
        and a superseded attempt's output must never shadow the current
        one. (Cloud regression 2026-08-14: a blocked attempt's report hid
        the successful retry's result from the analysis task, which then
        proposed a redundant re-run.)
        """
        self.artifacts = [a for a in self.artifacts if a.id != artifact.id]
        self.artifacts.append(artifact)
        if task is not None:
            task.artifacts = [aid for aid in task.artifacts if aid != artifact.id]
            task.artifacts.append(artifact.id)

    def next_task_number(self) -> int:
        """Return the next globally unique numeric task suffix."""
        numbers = []
        for task in self.tasks:
            prefix, separator, suffix = task.id.partition("_")
            if prefix == "task" and separator and suffix.isdigit():
                numbers.append(int(suffix))
        return max(numbers, default=0) + 1

    def next_decision_number(self) -> int:
        """Return the next decision number (1-based)."""
        return len(self.decisions) + 1

    def next_artifact_number(self) -> int:
        """Return the next globally unique numeric artifact suffix.

        Max-based (like next_task_number), robust to register_artifact
        replacements shrinking the list.
        """
        numbers = []
        for artifact in self.artifacts:
            *_, suffix = artifact.id.rpartition("_")
            if suffix.isdigit():
                numbers.append(int(suffix))
        return max(numbers, default=0) + 1
