"""Agentic loop controller — the main run loop of ResAgent.

Observes state, picks actions via Planner, executes via adapters,
records observations, and repeats until finish or blocked.
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
from .policies.retry import classify_transient
from .state import save_state
from .workspace_layout import WorkspaceLayout


class Controller:
    """The main agentic loop. Owns adapters and planner, drives the loop."""

    def __init__(
        self,
        planner: Planner,
        expagent: ExpAgentAdapter,
        codingagent: CodingAgentAdapter,
        reproagent: ReproAgentAdapter,
        confirm_callback: callable = None,  # called as confirm(msg) -> bool
    ):
        self.planner = planner
        self.expagent = expagent
        self.codingagent = codingagent
        self.reproagent = reproagent
        self.confirm = confirm_callback or (lambda _: True)

    def step(self, state: ResearchState) -> Observation:
        """One iteration of the agentic loop.

        1. Planner chooses an action based on current state.
        2. Controller executes the action via the appropriate adapter.
        3. State is updated with the observation.
        """
        planned = self.planner.choose_action(state)

        # Record decision
        dec_id = f"decision_{state.next_decision_number():03d}"
        decision = DecisionRecord(
            id=dec_id,
            made_by="ResAgent",
            reason=planned.reason,
            selected_action=planned.action.value,
            alternatives=[],
            evidence=[],
        )
        state.decisions.append(decision)

        # Execute
        observation = self._execute(state, planned)

        state.observations.append(observation)
        state.budget.api_calls_used += 1

        # Update run-level status based on terminal actions
        if observation.action == ActionName.finish:
            state.run.status = RunStatus.completed
            state.current_summary = observation.detail
        elif observation.result == "user_response_required":
            state.run.status = RunStatus.paused

        return observation

    def run(self, state: ResearchState, max_steps: int = 50) -> ResearchState:
        """Run the full agentic loop until finish or max steps."""
        for _ in range(max_steps):
            obs = self.step(state)
            save_state(state)

            if obs.action == ActionName.finish:
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
        result = self.expagent.advise(state, layout)
        state.artifacts.append(result["artifact"])

        for task in result.get("tasks", []):
            state.tasks.append(task)

        return Observation(
            action=ActionName.call_exp_agent,
            result="ok",
            detail=planned.analysis,
            artifact_ids=[result["artifact"].id],
            task_ids=[t.id for t in result.get("tasks", [])],
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
            result = self.codingagent.execute(task, layout)
            state.artifacts.append(result["artifact"])
            task.artifacts.append(result["artifact"].id)
            task.status = TaskStatus.completed
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].artifacts.append(result["artifact"].id)
            state.budget.tasks_run += 1

            return Observation(
                action=ActionName.call_coding_agent,
                result="ok",
                detail=f"Coding task {task.id} completed.",
                artifact_ids=[result["artifact"].id],
                task_ids=[task.id],
            )
        except Exception as e:
            task.status = TaskStatus.failed
            task.error = str(e)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].error = str(e)
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
            result = self.reproagent.execute(task, layout)
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
            return Observation(
                action=ActionName.call_repro_agent,
                result="error",
                detail=str(e),
                task_ids=[task.id],
            )

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
            task_id=planned.params.get("task_id"),
            requested_fields=planned.params.get("requested_fields", []),
        )

        return Observation(
            action=ActionName.ask_user,
            result="user_response_required",
            detail=f"Question: {question_text[:200]}",
        )

    def _handle_finish(self, state, planned, layout) -> Observation:
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
