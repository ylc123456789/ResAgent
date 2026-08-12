"""Agentic loop controller — the main run loop of ResAgent.

Observes state, picks actions via Planner, executes via adapters,
records observations, and repeats until finish, max_steps, or user_response_required (paused).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    ResearchState, AgentTask, Attempt, PendingQuestion, DecisionRecord,
    Observation, ActionName, Producer, TaskStatus, AgentKind, RunStatus,
)
from .planner import Planner, PlannedAction
from .adapters.expagent import ExpAgentAdapter
from .adapters.codingagent import CodingAgentAdapter
from .adapters.reproagent import ReproAgentAdapter
from .policies.retry import RetryPolicy, classify_transient
from .state import save_state
from .task_contracts import TERMINAL_RUN_STATUSES, validate_finish
from .workspace_layout import WorkspaceLayout


class Controller:
    """The main agentic loop. Owns adapters and planner, drives the loop."""

    def __init__(
        self,
        planner: Planner,
        expagent: ExpAgentAdapter,
        codingagent: CodingAgentAdapter,
        reproagent: ReproAgentAdapter,
        confirm_callback: callable = None,
    ):
        self.planner = planner
        self.expagent = expagent
        self.codingagent = codingagent
        self.reproagent = reproagent
        self.confirm = confirm_callback or (lambda _: True)

    def step(self, state: ResearchState) -> Observation:
        """Execute one action, respecting persisted pause and retry state."""
        if state.run.status in TERMINAL_RUN_STATUSES:
            observation = Observation(
                action=ActionName.finish,
                result="terminal",
                detail=f"Run is already {state.run.status.value}.",
            )
            state.observations.append(observation)
            return observation

        if state.pending_question is not None or state.run.status == RunStatus.paused:
            observation = Observation(
                action=ActionName.ask_user,
                result="user_response_required",
                detail="Run is paused until the pending question is answered.",
            )
            state.observations.append(observation)
            state.run.status = RunStatus.paused
            return observation

        planned = self._next_retry_action(state) or self.planner.choose_action(state)
        dec_id = f"decision_{state.next_decision_number():03d}"
        state.decisions.append(DecisionRecord(
            id=dec_id,
            made_by="ResAgent",
            reason=planned.reason,
            selected_action=planned.action.value,
        ))
        observation = self._execute(state, planned)
        state.observations.append(observation)
        state.budget.api_calls_used += 1

        if observation.action == ActionName.finish and observation.result == "ok":
            state.run.status = RunStatus.completed
            state.current_summary = observation.detail
        elif observation.result == "user_response_required":
            state.run.status = RunStatus.paused
        return observation

    def run(self, state: ResearchState, max_steps: int = 50) -> ResearchState:
        """Run the full agentic loop until finish, max_steps, or pause (user_response_required)."""
        for _ in range(max_steps):
            obs = self.step(state)
            save_state(state)

            if obs.action == ActionName.finish and obs.result in {"ok", "terminal"}:
                save_state(state)
                break

            if obs.result == "user_response_required":
                save_state(state)
                break

        return state

    # -- action execution ---------------------------------------------------

    def _execute(self, state: ResearchState, planned: PlannedAction) -> Observation:
        layout = WorkspaceLayout(state.run.workspace_dir, state.run.run_id)

        handlers = {
            ActionName.call_exp_agent: self._handle_exp_agent,
            ActionName.call_coding_agent: self._handle_coding_agent,
            ActionName.call_repro_agent: self._handle_repro_agent,
            ActionName.classify_failure: self._handle_classify_failure,
            ActionName.ask_user: self._handle_ask_user,
            ActionName.finish: self._handle_finish,
        }

        handler = handlers.get(planned.action, self._handle_unknown)
        return handler(state, planned, layout)

    def _handle_exp_agent(self, state, planned, layout) -> Observation:
        task = None
        task_id = planned.params.get("task_id", "")
        pending = [
            t.id for t in state.tasks
            if t.agent == Producer.ExpAgent and t.status == TaskStatus.pending
        ]
        if not task_id and pending:
            return Observation(
                action=ActionName.call_exp_agent,
                result="error",
                detail=f"ExpAgent task_id is required. Pending: {pending}",
                task_ids=pending,
            )
        if task_id:
            task = self._require_task(state, planned, Producer.ExpAgent)
            if isinstance(task, Observation):
                return task
            task.status = TaskStatus.running
            task.attempts.append(Attempt(
                attempt_number=len(task.attempts) + 1,
                started_at=datetime.now(timezone.utc),
            ))

        result = self.expagent.advise(state, layout)
        artifact = result["artifact"]
        state.artifacts.append(artifact)
        for spawned in result.get("tasks", []):
            supersedes = spawned.input.get("supersedes_task_id", "")
            previous = state.find_task(supersedes) if supersedes else None
            if previous is not None and previous.status in (TaskStatus.pending, TaskStatus.failed, TaskStatus.blocked):
                previous.status = TaskStatus.skipped
                previous.error = f"Superseded by {spawned.id}."
            state.tasks.append(spawned)

        issues = result["raw"].get("_normalization_issues", [])
        task_ids = [t.id for t in result.get("tasks", [])]
        if task is not None:
            task.status = TaskStatus.completed
            task.artifacts.append(artifact.id)
            task.attempts[-1].artifacts.append(artifact.id)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task_ids.insert(0, task.id)
            state.budget.tasks_run += 1

        return Observation(
            action=ActionName.call_exp_agent,
            result="error" if issues else "ok",
            detail=(
                "Task contract validation failed: " + "; ".join(issues)
                if issues else planned.analysis or result["raw"].get("summary", "")
            ),
            artifact_ids=[artifact.id],
            task_ids=task_ids,
        )

    def _handle_coding_agent(self, state, planned, layout) -> Observation:
        task = self._require_task(state, planned, Producer.CodingAgent)
        if isinstance(task, Observation):
            return task  # error observation from _require_task

        task.status = TaskStatus.running
        attempt_num = len(task.attempts) + 1
        task.attempts.append(Attempt(attempt_number=attempt_num,
                                    started_at=datetime.now(timezone.utc)))

        try:
            result = self.codingagent.execute(task, layout, attempt_num)
            state.artifacts.append(result["artifact"])
            task.artifacts.append(result["artifact"].id)
            outcome = result.get("outcome", "completed")
            if outcome in {"completed", "completed_with_warnings"}:
                task.status = TaskStatus.completed
            elif outcome == "blocked":
                task.status = TaskStatus.blocked
            elif outcome == "needs_user_input":
                task.status = TaskStatus.needs_user_input
            else:
                task.status = TaskStatus.failed
                self._schedule_retry(state, task, str(result["raw"].get("summary", "CodingAgent failed")))
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].artifacts.append(result["artifact"].id)
            state.budget.tasks_run += 1

            return Observation(
                action=ActionName.call_coding_agent,
                result="ok" if task.status == TaskStatus.completed else ("user_response_required" if task.status == TaskStatus.needs_user_input else "error"),
                detail=result["raw"].get("summary", f"Coding task {task.id} {task.status.value}."),
                artifact_ids=[result["artifact"].id],
                task_ids=[task.id],
            )
        except Exception as e:
            task.status = TaskStatus.failed
            task.error = str(e)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].error = str(e)
            self._schedule_retry(state, task, str(e))
            return Observation(
                action=ActionName.call_coding_agent,
                result="error",
                detail=str(e),
                task_ids=[task.id],
            )

    def _handle_repro_agent(self, state, planned, layout) -> Observation:
        task = self._require_task(state, planned, Producer.ReproAgent)
        if isinstance(task, Observation):
            return task

        task.status = TaskStatus.running
        attempt_num = len(task.attempts) + 1
        task.attempts.append(Attempt(attempt_number=attempt_num,
                                    started_at=datetime.now(timezone.utc)))

        try:
            result = self.reproagent.execute(task, layout, attempt_num)
            state.artifacts.append(result["artifact"])
            task.artifacts.append(result["artifact"].id)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].artifacts.append(result["artifact"].id)
            state.budget.tasks_run += 1

            outcome = result.get("outcome", result.get("returncode") == 0 and "completed" or "failed")
            if outcome == "completed":
                task.status = TaskStatus.completed
                obs_result = "ok"
            elif outcome == "completed_with_warnings":
                task.status = TaskStatus.completed
                task.error = result["raw"].get("summary", "")[:200]
                obs_result = "ok"
            elif outcome == "blocked":
                task.status = TaskStatus.blocked
                obs_result = "error"
            elif outcome == "needs_user_input":
                task.status = TaskStatus.needs_user_input
                obs_result = "user_response_required"
            else:
                task.status = TaskStatus.failed
                self._schedule_retry(state, task, str(result["raw"].get("summary", "ReproAgent failed")))
                obs_result = "error"

            return Observation(
                action=ActionName.call_repro_agent,
                result=obs_result,
                detail=result["raw"].get("summary", ""),
                artifact_ids=[result["artifact"].id],
                task_ids=[task.id],
            )
        except Exception as e:
            task.status = TaskStatus.failed
            task.error = str(e)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].error = str(e)
            self._schedule_retry(state, task, str(e))
            return Observation(
                action=ActionName.call_repro_agent,
                result="error",
                detail=str(e),
                task_ids=[task.id],
            )

    def _next_retry_action(self, state: ResearchState) -> PlannedAction | None:
        """Retry transient failures before asking the planner for a new task."""
        for task in state.tasks:
            if not task.input.get("_retry_scheduled"):
                continue
            action = {
                Producer.CodingAgent: ActionName.call_coding_agent,
                Producer.ReproAgent: ActionName.call_repro_agent,
            }.get(task.agent)
            if action is None or task.status != TaskStatus.pending:
                continue
            task.input.pop("_retry_scheduled", None)
            return PlannedAction(
                action=action,
                params={"task_id": task.id, **task.input},
                reason=f"Retrying transient failure for {task.id}.",
                analysis="Retry is prioritized over new planning.",
            )
        return None

    def _schedule_retry(self, state, task, error: str) -> bool:
        """Schedule a bounded transient retry for the next controller step."""
        task.error = error
        task.attempts[-1].error = error
        if RetryPolicy(state.budget.max_task_retries).should_retry(task, error):
            task.status = TaskStatus.pending
            task.input["_retry_scheduled"] = True
            return True
        return False

    def _require_task(self, state, planned, expected_agent):
        """Validate task_id and return the task, or an error Observation."""
        task_id = planned.params.get("task_id", "")
        if not task_id:
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Missing task_id. {expected_agent.value} calls require a task_id from the pending task list.",
            )
        task = state.find_task(task_id)
        if task is None:
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Task {task_id} not found. Available: {[t.id for t in state.tasks]}",
            )
        if task.agent != expected_agent:
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Task {task_id} belongs to {task.agent.value}, not {expected_agent.value}.",
            )
        if task.status not in (TaskStatus.pending, TaskStatus.failed, TaskStatus.blocked):
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Task {task_id} is {task.status.value}, not pending/failed/blocked.",
            )
        return task

    def _handle_classify_failure(self, state, planned, layout) -> Observation:
        task_id = planned.params.get("task_id", "")
        error = planned.params.get("error_message", "")

        # Deterministic classifier first — avoids LLM call for known network errors
        classification = classify_transient(error)
        if classification.get("category") == "unknown":
            classification = self.planner.classify_failure(task_id, error)

        detail = (
            f"Failure classified as: {classification.get('category', 'unknown')}. "
            f"Recommended: {classification.get('recommended_action', 'investigate')}."
        )

        return Observation(
            action=ActionName.classify_failure,
            result="ok",
            detail=detail,
            task_ids=[task_id] if task_id else [],
        )

    def _handle_ask_user(self, state, planned, layout) -> Observation:
        question_text = planned.params.get("question", "Continue?")

        task_id = planned.params.get("task_id", "")
        if task_id:
            task = self._require_task(state, planned, Producer.ResAgent)
            if isinstance(task, Observation):
                return task
            task.status = TaskStatus.needs_user_input
            question_text = task.input.get("question") or question_text

        # Check if same question is already pending — no duplicate
        if state.pending_question is not None:
            return Observation(
                action=ActionName.ask_user,
                result="user_response_required",
                detail=f"Question already pending: {state.pending_question.text[:200]}",
            )

        # Create persisted question and pause
        import uuid
        state.pending_question = PendingQuestion(
            question_id=f"q_{uuid.uuid4().hex[:8]}",
            text=question_text,
            task_id=task_id or None,
            requested_fields=planned.params.get("requested_fields", []),
        )

        return Observation(
            action=ActionName.ask_user,
            result="user_response_required",
            detail=f"Question: {question_text[:200]}",
        )

    def _handle_finish(self, state, planned, layout) -> Observation:
        check = validate_finish(state)
        if not check.allowed:
            return Observation(
                action=ActionName.finish,
                result="rejected",
                detail=f"Cannot finish: {check.reason}.",
                task_ids=list(check.task_ids),
            )
        return Observation(
            action=ActionName.finish,
            result="ok",
            detail=planned.params.get("summary", "Run finished."),
        )

    def _handle_unknown(self, state, planned, layout) -> Observation:
        return Observation(
            action=planned.action,
            result="error",
            detail=f"Unknown action: {planned.action}",
        )
