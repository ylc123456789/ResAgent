"""Agentic loop controller — the main run loop of ResAgent.

Observes state, picks actions via Planner, executes via adapters,
records observations, and repeats until finish or blocked.
"""

from __future__ import annotations

from pathlib import Path

from .models import (
    ResearchState, AgentTask, DecisionRecord, Observation,
    ActionName, Producer, TaskStatus, AgentKind, RunStatus,
)
from .planner import Planner, PlannedAction
from .adapters.expagent import ExpAgentAdapter
from .adapters.codingagent import CodingAgentAdapter
from .adapters.reproagent import ReproAgentAdapter
from .state import save_state


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
        ws = str(Path(state.run.workspace_dir) / state.run.run_id)

        handlers = {
            ActionName.call_exp_agent: self._handle_exp_agent,
            ActionName.call_coding_agent: self._handle_coding_agent,
            ActionName.call_repro_agent: self._handle_repro_agent,
            ActionName.classify_failure: self._handle_classify_failure,
            ActionName.ask_user: self._handle_ask_user,
            ActionName.finish: self._handle_finish,
        }

        handler = handlers.get(planned.action, self._handle_unknown)
        return handler(state, planned, ws)

    def _handle_exp_agent(self, state, planned, ws) -> Observation:
        result = self.expagent.advise(state, ws)
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

    def _handle_coding_agent(self, state, planned, ws) -> Observation:
        task_id = planned.params.get("task_id", "")
        task = state.find_task(task_id)
        if task:
            task.status = TaskStatus.running

        try:
            # Find or create task
            if not task:
                task = AgentTask(
                    id=f"task_{state.next_task_number():03d}",
                    agent=Producer.CodingAgent,
                    kind=AgentKind.coding_task,
                    input=planned.params,
                )
                state.tasks.append(task)
                task.status = TaskStatus.running

            result = self.codingagent.execute(task, ws)
            state.artifacts.append(result["artifact"])
            task.status = TaskStatus.completed
            task.artifacts.append(result["artifact"].id)
            state.budget.tasks_run += 1

            return Observation(
                action=ActionName.call_coding_agent,
                result="ok",
                detail=f"Coding task {task.id} completed.",
                artifact_ids=[result["artifact"].id],
                task_ids=[task.id],
            )
        except Exception as e:
            if task:
                task.status = TaskStatus.failed
                task.error = str(e)
            return Observation(
                action=ActionName.call_coding_agent,
                result="error",
                detail=str(e),
                task_ids=[task.id] if task else [],
            )

    def _handle_repro_agent(self, state, planned, ws) -> Observation:
        task_id = planned.params.get("task_id", "")
        task = state.find_task(task_id)
        if task:
            task.status = TaskStatus.running

        try:
            if not task:
                task = AgentTask(
                    id=f"task_{state.next_task_number():03d}",
                    agent=Producer.ReproAgent,
                    kind=AgentKind.repro_task,
                    input=planned.params,
                )
                state.tasks.append(task)
                task.status = TaskStatus.running

            result = self.reproagent.execute(task, ws)
            state.artifacts.append(result["artifact"])
            task.status = TaskStatus.completed
            task.artifacts.append(result["artifact"].id)
            state.budget.tasks_run += 1

            return Observation(
                action=ActionName.call_repro_agent,
                result="ok" if result["returncode"] == 0 else "error",
                detail=result["raw"].get("summary", ""),
                artifact_ids=[result["artifact"].id],
                task_ids=[task.id],
            )
        except Exception as e:
            if task:
                task.status = TaskStatus.failed
                task.error = str(e)
            return Observation(
                action=ActionName.call_repro_agent,
                result="error",
                detail=str(e),
                task_ids=[task.id] if task else [],
            )

    def _handle_classify_failure(self, state, planned, ws) -> Observation:
        task_id = planned.params.get("task_id", "")
        error = planned.params.get("error_message", "")
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

    def _handle_ask_user(self, state, planned, ws) -> Observation:
        question = planned.params.get("question", "Continue?")
        approved = self.confirm(question)

        if approved:
            return Observation(
                action=ActionName.ask_user,
                result="ok",
                detail=f"User approved: {question[:200]}",
            )
        else:
            return Observation(
                action=ActionName.ask_user,
                result="user_response_required",
                detail=f"User declined: {question[:200]}",
            )

    def _handle_finish(self, state, planned, ws) -> Observation:
        return Observation(
            action=ActionName.finish,
            result="ok",
            detail=planned.params.get("summary", "Run finished."),
        )

    def _handle_unknown(self, state, planned, ws) -> Observation:
        return Observation(
            action=planned.action,
            result="error",
            detail=f"Unknown action: {planned.action}",
        )
