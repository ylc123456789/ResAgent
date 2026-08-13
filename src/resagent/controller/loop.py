"""Agentic loop controller - the main run loop of ResAgent.

Observes state, picks actions via Planner, executes via adapters,
records observations, and repeats until finish, max_steps, or user_response_required (paused).
"""

from __future__ import annotations

from ..models import ResearchState, DecisionRecord, Observation, ActionName, RunStatus
from .planner import Planner, PlannedAction
from .actions import ControllerActions
from ..adapters.expagent import ExpAgentAdapter
from ..adapters.codingagent import CodingAgentAdapter
from ..adapters.reproagent import ReproAgentAdapter
from ..persistence.state import save_state
from .contracts import TERMINAL_RUN_STATUSES


class Controller(ControllerActions):
    """The main agentic loop. Owns adapters and planner, drives the loop."""

    def __init__(
        self,
        planner: Planner,
        expagent: ExpAgentAdapter,
        codingagent: CodingAgentAdapter,
        reproagent: ReproAgentAdapter,
        confirm_callback: callable = None,
        shared_workspace: str = "auto",
    ):
        self.planner = planner
        self.expagent = expagent
        self.codingagent = codingagent
        self.reproagent = reproagent
        self.confirm = confirm_callback or (lambda _: True)
        self.shared_workspace = shared_workspace

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
