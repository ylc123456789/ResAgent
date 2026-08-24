"""Experiment delivery requirements survive orchestration boundaries."""

from resagent.adapters.expagent.task_conversion import actions_to_tasks
from resagent.context import build_reproagent_context
from resagent.controller.contracts import final_acceptance_issues
from resagent.models import (
    AgentKind, AgentTask, Artifact, ArtifactType, Producer, TaskStatus,
)
from resagent.persistence.state import init_state
from tests.v2_registry import make_registry


def test_experiment_delivery_contract_is_preserved(tmp_path):
    state = init_state("delivery", str(tmp_path), "run experiment")
    tasks, issues = actions_to_tasks(
        [{
            "action_id": "run",
            "capability": "execute_experiment",
            "objective": "measure",
            "rationale": "collect evidence",
            "depends_on": [],
            "project_ref": "project",
            "required": True,
            "success_criteria": ["accuracy recorded"],
            "expected_metrics": ["accuracy"],
            "expected_artifacts": ["final_metrics.json"],
            "requires_gpu": False,
        }],
        state,
        "decision",
        1,
        registry=make_registry(),
    )

    assert issues == []
    task = tasks[0]
    assert task.input["expected_metrics"] == ["accuracy"]
    assert task.input["expected_artifacts"] == ["final_metrics.json"]
    assert task.input["success_criteria"] == ["accuracy recorded"]


def test_reproagent_context_preserves_delivery_contract():
    task = AgentTask(
        id="task_001",
        agent=Producer.ReproAgent,
        kind=AgentKind.repro_task,
        input={
            "expected_metrics": ["accuracy"],
            "expected_artifacts": ["final_metrics.json"],
            "success_criteria": ["accuracy recorded"],
        },
    )

    context = build_reproagent_context(task)

    assert context["expected_metrics"] == ["accuracy"]
    assert context["expected_artifacts"] == ["final_metrics.json"]
    assert context["success_criteria"] == ["accuracy recorded"]


def test_analysis_delivery_contract_is_preserved(tmp_path):
    state = init_state("analysis-delivery", str(tmp_path), "analyze results")
    tasks, issues = actions_to_tasks(
        [
            {
                "action_id": "run",
                "capability": "execute_experiment",
                "objective": "measure",
                "rationale": "collect evidence",
                "depends_on": [],
                "project_ref": "project",
                "required": True,
                "expected_metrics": ["accuracy"],
            },
            {
                "action_id": "analyze",
                "capability": "analyze_results",
                "objective": "compare results",
                "rationale": "draw a conclusion",
                "depends_on": ["run"],
                "project_ref": "project",
                "required": True,
                "success_criteria": ["write the final metrics file"],
                "expected_artifacts": ["final_metrics.json"],
            },
        ],
        state,
        "decision",
        1,
        registry=make_registry(),
    )

    assert issues == []
    assert tasks[1].input["expected_artifacts"] == ["final_metrics.json"]
    assert tasks[1].input["success_criteria"] == ["write the final metrics file"]


def test_final_acceptance_checks_promised_artifacts_from_registered_outputs(tmp_path):
    state = init_state(
        "artifact-acceptance",
        str(tmp_path),
        "produce final_metrics.json",
    )
    decision = Artifact(
        id="decision",
        type=ArtifactType.scientific_decision,
        producer=Producer.ExpAgent,
        path="tasks/expagent/scientific_decision.json",
        metadata={"raw_decision": {"needs_user_input": []}},
    )
    analysis = AgentTask(
        id="task_001",
        agent=Producer.ExpAgent,
        kind=AgentKind.advise,
        capability="analyze_results",
        status=TaskStatus.completed,
        input={
            "expected_artifacts": [
                "final_metrics.json",
                "advisor_invented_report.md",
            ],
        },
        artifacts=[decision.id],
    )
    state.artifacts.append(decision)
    state.tasks.append(analysis)

    assert final_acceptance_issues(state) == (
        "Missing required artifact: final_metrics.json",
    )

    repair = AgentTask(
        id="task_002",
        agent=Producer.CodingAgent,
        kind=AgentKind.coding_task,
        status=TaskStatus.completed,
        artifacts=["code-result"],
    )
    state.tasks.append(repair)

    state.artifacts.append(Artifact(
        id="code-result",
        type=ArtifactType.code_patch,
        producer=Producer.CodingAgent,
        path="tasks/codingagent/patch_report.md",
        metadata={
            "raw_result": {"changed_files": ["repo/final_metrics.json"]},
        },
    ))

    assert final_acceptance_issues(state) == ()
