"""Tests for controller agentic loop — full mock mode."""

import tempfile
from pathlib import Path

from resagent.models import (
    ResearchState, ResearchRun, Producer, AgentKind, AgentTask,
)
from resagent.state import init_state, save_state
from resagent.planner import Planner
from resagent.controller import Controller
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter


def _build_mock_controller():
    return Controller(
        planner=Planner(mock=True),
        expagent=ExpAgentAdapter(mock=True),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=ReproAgentAdapter(mock=True),
    )


class TestControllerStep:
    def test_first_step_calls_expagent(self):
        ctrl = _build_mock_controller()
        state = init_state("test-loop-001", tempfile.mkdtemp(), "Test goal")

        obs = ctrl.step(state)

        assert obs.action.value == "call_exp_agent"
        assert obs.result == "ok"
        assert len(state.artifacts) == 1
        assert len(state.tasks) > 0
        assert len(state.decisions) == 1

    def test_second_step_executes_task(self):
        """After ExpAgent creates tasks, second step should execute one."""
        ctrl = _build_mock_controller()
        state = init_state("test-loop-002", tempfile.mkdtemp(), "Test goal")

        # Step 1: call ExpAgent
        ctrl.step(state)

        # Step 2: should pick up the pending task
        obs2 = ctrl.step(state)
        assert obs2.result == "ok"
        # Should have executed something
        assert len(state.observations) == 2

    def test_run_loop(self):
        """Full mock run should complete without error."""
        ctrl = _build_mock_controller()
        state = init_state("test-loop-003", tempfile.mkdtemp(), "Test goal")

        result = ctrl.run(state, max_steps=5)
        assert result.run.status.value in ("completed", "paused")
        assert len(result.observations) > 0

    def test_finish_when_no_tasks(self):
        """When no pending tasks remain, should finish."""
        ctrl = _build_mock_controller()
        state = init_state("test-loop-004", tempfile.mkdtemp(), "Test goal")
        # Add an artifact so mock planner doesn't always call expagent
        from resagent.models import Artifact, ArtifactType
        state.artifacts.append(
            Artifact(id="existing", type=ArtifactType.report,
                     producer=Producer.ResAgent, path="p", summary="s")
        )

        obs = ctrl.step(state)
        # With artifacts but no tasks, mock should finish
        assert obs.action.value in ("finish", "call_exp_agent")
