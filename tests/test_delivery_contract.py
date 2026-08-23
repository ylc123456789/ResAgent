"""Experiment delivery requirements survive orchestration boundaries."""

from resagent.adapters.expagent.task_conversion import actions_to_tasks
from resagent.context import build_reproagent_context
from resagent.models import AgentKind, AgentTask, Producer
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
