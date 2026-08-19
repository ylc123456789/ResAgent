"""Tests for controller agentic loop — full mock mode."""

import tempfile
from pathlib import Path

from resagent.models import (
    ResearchState, ResearchRun, Producer, AgentKind, AgentTask,
)
from resagent.persistence.state import init_state, save_state
from resagent.controller.planner import Planner
from resagent.controller import Controller
from resagent.adapters.expagent import ExpAgentAdapter
from tests.v2_registry import make_registry
from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter


def _build_mock_controller():
    return Controller(
        planner=Planner(mock=True),
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
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
        assert result.run.status.value in ("completed", "paused", "interrupted")
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
    from resagent.controller.planner import PlannedAction

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
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
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
    from resagent.controller.planner import PlannedAction

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
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
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
    from resagent.persistence.state import submit_user_response

    state = init_state("answer-run", str(tmp_path), "Goal")
    state.run.status = RunStatus.paused
    state.pending_question = PendingQuestion(question_id="q_001", text="Proceed?")

    submit_user_response(state, "q_001", "yes, proceed")

    assert state.pending_question is None
    assert state.run.status == RunStatus.running
    assert state.answered_questions[-1].response == "yes, proceed"


def test_submit_user_response_becomes_user_directive(tmp_path):
    """The answer must reach user_directives so the controller prompt actually
    sees and obeys it (regression: answers used to dead-end in answered_questions)."""
    from resagent.models import PendingQuestion
    from resagent.persistence.state import submit_user_response

    state = init_state("answer-run", str(tmp_path), "Goal")
    state.pending_question = PendingQuestion(question_id="q_001", text="Proceed?")

    submit_user_response(state, "q_001", "stop now")

    assert state.user_directives[-1].text == "stop now"


def test_paused_run_never_calls_planner(tmp_path):
    from resagent.models import PendingQuestion, RunStatus

    class PanicPlanner:
        def choose_action(self, state):
            raise AssertionError("planner must not run while paused")

    state = init_state("paused-run", str(tmp_path), "Goal")
    state.run.status = RunStatus.paused
    state.pending_question = PendingQuestion(question_id="q_001", text="Proceed?")
    ctrl = Controller(PanicPlanner(), ExpAgentAdapter(mock=True, registry=make_registry()), CodingAgentAdapter(mock=True), ReproAgentAdapter(mock=True))

    obs = ctrl.step(state)

    assert obs.result == "user_response_required"
    assert state.run.status == RunStatus.paused


def test_transient_retry_runs_attempt_two_before_new_planning(tmp_path):
    from resagent.models import ActionName, Artifact, ArtifactType, TaskStatus
    from resagent.controller.planner import PlannedAction

    class OneRetryRepro:
        def __init__(self):
            self.calls = 0

        def execute(self, task, layout, attempt_number):
            self.calls += 1
            artifact = Artifact(id=f"repro_{attempt_number}", type=ArtifactType.repro_result,
                                producer=Producer.ReproAgent, path=f"attempt_{attempt_number}.md")
            if self.calls == 1:
                return {"artifact": artifact, "outcome": "failed", "raw": {"summary": "connection timed out"}}
            return {"artifact": artifact, "outcome": "completed", "raw": {"summary": "completed"}}

    class OnePlanningCall:
        def __init__(self, action):
            self.action = action
            self.calls = 0

        def choose_action(self, state):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("retry should run before new planning")
            return self.action

    state = init_state("retry-priority", str(tmp_path), "Reproduce baseline")
    task = AgentTask(id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task)
    state.tasks.append(task)
    planner = OnePlanningCall(PlannedAction(ActionName.call_repro_agent, {"task_id": task.id}))
    repro = OneRetryRepro()
    ctrl = Controller(planner, ExpAgentAdapter(mock=True, registry=make_registry()), CodingAgentAdapter(mock=True), repro)

    ctrl.step(state)
    ctrl.step(state)

    assert planner.calls == 1
    assert repro.calls == 2
    assert task.status == TaskStatus.completed
    assert [a.attempt_number for a in task.attempts] == [1, 2]


def test_step_limit_is_persisted_as_interrupted(tmp_path):
    from resagent.models import ActionName, RunStatus
    from resagent.controller.planner import PlannedAction

    state = init_state("step-limit", str(tmp_path), "goal")
    ctrl = Controller(
        _FixedPlanner(PlannedAction(
            ActionName.call_coding_agent, {"task_id": "missing"},
        )),
        ExpAgentAdapter(mock=True, registry=make_registry()), CodingAgentAdapter(mock=True),
        ReproAgentAdapter(mock=True),
    )

    result = ctrl.run(state, max_steps=2)

    assert result.run.status == RunStatus.interrupted
    assert "2-step" in result.current_summary


def test_expagent_task_receives_all_dependency_artifacts(tmp_path):
    from resagent.models import ActionName, Artifact, ArtifactType, TaskStatus
    from resagent.controller.planner import PlannedAction

    class CapturingExpAgent:
        def __init__(self):
            self.task = None

        def advise(self, state, layout, task=None):
            self.task = task
            return {
                "artifact": Artifact(
                    id="analysis", type=ArtifactType.scientific_decision,
                    producer=Producer.ExpAgent, path="analysis.json",
                ),
                "tasks": [],
                "raw": {"summary": "comparison complete"},
            }

    state = init_state("fan-in-dispatch", str(tmp_path), "compare two runs")
    run_root = tmp_path / state.run.run_id
    artifacts = []
    dependencies = []
    for number in (1, 2):
        path = run_root / f"result_{number}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"accuracy=0.{number + 7}", encoding="utf-8")
        artifact = Artifact(
            id=f"result_{number}", type=ArtifactType.repro_result,
            producer=Producer.ReproAgent,
            path=str(path.relative_to(run_root)),
        )
        dependency = AgentTask(
            id=f"task_{number:03d}", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task, status=TaskStatus.completed,
            artifacts=[artifact.id],
        )
        artifacts.append(artifact)
        dependencies.append(dependency)
    analysis = AgentTask(
        id="task_003", agent=Producer.ExpAgent, kind=AgentKind.advise,
        input={"task_goal": "compare accuracy"},
        depends_on=[task.id for task in dependencies],
    )
    state.artifacts.extend(artifacts)
    state.tasks.extend([*dependencies, analysis])
    expagent = CapturingExpAgent()
    controller = Controller(
        _FixedPlanner(PlannedAction(
            ActionName.call_exp_agent, {"task_id": analysis.id},
        )),
        expagent, CodingAgentAdapter(mock=True), ReproAgentAdapter(mock=True),
    )

    observation = controller.step(state)

    assert observation.result == "ok"
    assert analysis.status == TaskStatus.completed
    assert [item["artifact_id"] for item in expagent.task.input["input_artifacts"]] == [
        "result_1", "result_2",
    ]
