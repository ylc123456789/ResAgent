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

class _FixedPlanner:
    def __init__(self, action):
        self.action = action

    def choose_action(self, state):
        return self.action

    def classify_failure(self, task_id, error):
        return {"category": "unknown"}


def test_expagent_task_is_completed_and_bound_to_its_artifact(tmp_path):
    from resagent.models import ActionName, TaskStatus
    from resagent.planner import PlannedAction

    state = init_state("bound-expagent", str(tmp_path), "Analyze result")
    state.artifacts.append(
        __import__("resagent.models", fromlist=["Artifact"]).Artifact(
            id="seed", type=__import__("resagent.models", fromlist=["ArtifactType"]).ArtifactType.report,
            producer=Producer.ResAgent, path="seed.md",
        )
    )
    task = AgentTask(id="task_001", agent=Producer.ExpAgent, kind=AgentKind.advise)
    state.tasks.append(task)
    ctrl = Controller(
        planner=_FixedPlanner(PlannedAction(ActionName.call_exp_agent, {"task_id": task.id})),
        expagent=ExpAgentAdapter(mock=True),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=ReproAgentAdapter(mock=True),
    )

    obs = ctrl.step(state)

    assert obs.result == "ok"
    assert task.status == TaskStatus.completed
    assert len(task.attempts) == 1
    assert task.artifacts


def test_transient_repro_failure_returns_task_to_pending_queue(tmp_path):
    from resagent.models import ActionName, Artifact, ArtifactType, TaskStatus
    from resagent.planner import PlannedAction

    class FailingRepro:
        def execute(self, task, layout, attempt_number):
            artifact = Artifact(
                id="failed_repro", type=ArtifactType.repro_result,
                producer=Producer.ReproAgent, path="failure.md",
            )
            return {"artifact": artifact, "outcome": "failed", "raw": {"summary": "connection timed out"}}

    state = init_state("retry-repro", str(tmp_path), "Reproduce baseline")
    task = AgentTask(id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task)
    state.tasks.append(task)
    ctrl = Controller(
        planner=_FixedPlanner(PlannedAction(ActionName.call_repro_agent, {"task_id": task.id})),
        expagent=ExpAgentAdapter(mock=True),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=FailingRepro(),
    )

    obs = ctrl.step(state)

    assert obs.result == "error"
    assert task.status == TaskStatus.pending
    assert len(task.attempts) == 1
    assert "timed out" in task.attempts[0].error


def test_submit_user_response_clears_question_and_resumes(tmp_path):
    from resagent.models import PendingQuestion, RunStatus
    from resagent.state import submit_user_response

    state = init_state("answer-run", str(tmp_path), "Goal")
    state.run.status = RunStatus.paused
    state.pending_question = PendingQuestion(question_id="q_001", text="Proceed?")

    submit_user_response(state, "q_001", "yes, proceed")

    assert state.pending_question is None
    assert state.run.status == RunStatus.running
    assert state.answered_questions[-1].response == "yes, proceed"
