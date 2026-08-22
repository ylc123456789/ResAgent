"""Tests for context.py — context building for controller and adapters."""

from resagent.models import (
    ResearchState, ResearchRun, Artifact, AgentTask, DecisionRecord,
    Observation, ArtifactType, Producer, AgentKind, ActionName,
)
from resagent.context import (
    build_controller_context,
    build_codingagent_context, build_reproagent_context,
)


def _make_state() -> ResearchState:
    run = ResearchRun(
        run_id="test-001", workspace_dir="/tmp/runs",
        research_goal="Build a better MNIST classifier",
    )
    state = ResearchState(run=run, current_summary="Phase 1: baseline")
    state.artifacts.append(
        Artifact(id="a1", type=ArtifactType.scientific_decision,
                 producer=Producer.ExpAgent, path="p1",
                 summary="ExpAgent advised baseline first.")
    )
    state.tasks.append(
        AgentTask(id="t1", agent=Producer.CodingAgent,
                  kind=AgentKind.coding_task,
                  input={"repo_path": "/tmp/repo", "task_goal": "Add logging"})
    )
    state.decisions.append(
        DecisionRecord(id="d1", made_by="ResAgent", reason="Initial consult",
                       selected_action="call_exp_agent")
    )
    state.observations.append(
        Observation(action=ActionName.call_exp_agent, result="ok",
                    detail="ExpAgent returned advice")
    )
    return state


class TestControllerContext:
    def test_builds_without_error(self):
        state = _make_state()
        ctx = build_controller_context(state)
        assert "Build a better" in ctx
        assert "Research Goal" in ctx
        assert "t1" in ctx
        assert "a1" in ctx

    def test_empty_state(self):
        run = ResearchRun(run_id="e1", workspace_dir="/tmp", research_goal="Empty")
        state = ResearchState(run=run)
        ctx = build_controller_context(state)
        assert "Empty" in ctx


class TestAdapterContext:
    def test_codingagent_context(self):
        task = AgentTask(
            id="t1", agent=Producer.CodingAgent,
            kind=AgentKind.coding_task,
            input={"workspace_path": "/x", "task_goal": "fix bug",
                   "constraints": ["no refactor"], "verify_commands": ["pytest"]}
        )
        ctx = build_codingagent_context(task)
        assert ctx["workspace_path"] == "/x"
        assert ctx["task_goal"] == "fix bug"
        assert "no refactor" in ctx["constraints"]

    def test_reproagent_context(self):
        task = AgentTask(
            id="t1", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task,
            input={"paper_url": "https://arxiv.org/abs/1234",
                   "repo_url": "https://github.com/x/y",
                   "experiment_goal": "reproduce baseline"}
        )
        ctx = build_reproagent_context(task)
        assert ctx["paper_url"] == "https://arxiv.org/abs/1234"
        assert ctx["repo_url"] == "https://github.com/x/y"

    def test_reproagent_context_includes_dataset_cache(self):
        task = AgentTask(
            id="t1", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task,
            input={"paper_url": "p", "repo_url": "r",
                   "dataset_cache_dir": "/data/ds"}
        )
        ctx = build_reproagent_context(task)
        assert ctx["dataset_cache_dir"] == "/data/ds"
        # absent -> empty string, never dropped from the spec dict
        task2 = AgentTask(
            id="t2", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task, input={},
        )
        assert build_reproagent_context(task2)["dataset_cache_dir"] == ""

    def test_reproagent_context_exposes_structured_dependency_artifacts(self):
        task = AgentTask(
            id="t3", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
            input={
                "experiment_goal": "compare prior measurements",
                "env_name": "certified-env",
                "input_artifacts": [{
                    "path": "/runs/baseline.json",
                    "summary": "baseline accuracy 0.91",
                    "producer_task_id": "task_001",
                    "artifact_id": "baseline_result",
                }],
            },
        )

        ctx = build_reproagent_context(task)

        assert ctx["env_name"] == "certified-env"
        assert ctx["input_artifacts"] == [{
            "path": "/runs/baseline.json",
            "description": (
                "baseline accuracy 0.91; producer task task_001; "
                "artifact baseline_result"
            ),
        }]
        assert "/runs/baseline.json" in ctx["experiment_goal"]


def test_budget_enforcement_trims_low_priority_sections(tmp_path):
    """When context exceeds budget, lower-priority sections are trimmed."""
    run = ResearchRun(run_id="budget-run", workspace_dir=str(tmp_path), research_goal="Budget test")
    state = ResearchState(run=run)
    # Add many observations to blow past budget
    for i in range(200):
        state.observations.append(Observation(
            action=ActionName.call_exp_agent, result="ok",
            detail=f"Observation {i}: " + "x" * 1500,
        ))
    context = build_controller_context(state, model="deepseek-chat")  # 128K -> small budget
    # Should still contain critical sections
    assert "## Research Goal" in context
    assert "## Run Status" in context
    # Budget enforcement marker should appear
    assert "fit budget" in context or len(context) < 50000


def test_fallbacks_to_older_repro_when_latest_file_missing(tmp_path):
    """If newest repro_result file is gone, keep looking at older artifacts."""
    run = ResearchRun(run_id="fallback-run", workspace_dir=str(tmp_path), research_goal="Fallback")
    state = ResearchState(run=run)

    # Older artifact — file exists
    old = tmp_path / "fallback-run" / "tasks" / "repro" / "old_result.md"
    old.parent.mkdir(parents=True)
    old.write_text("older accuracy: 97.5%")

    # Newer artifact — file missing
    state.artifacts.append(Artifact(
        id="repro_002", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="tasks/repro/old_result.md", summary="old",
    ))
    state.artifacts.append(Artifact(
        id="repro_003", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="tasks/repro/missing.md", summary="this file does not exist",
    ))

    context = build_controller_context(state, model="deepseek-v4-pro")
    assert "97.5%" in context, "should fall back to older artifact when newest file is missing"


def test_latest_repro_result_keeps_final_metric_from_file(tmp_path):
    run = ResearchRun(run_id="metric-run", workspace_dir=str(tmp_path), research_goal="Verify accuracy")
    state = ResearchState(run=run)
    result = tmp_path / "metric-run" / "tasks" / "repro" / "result.md"
    result.parent.mkdir(parents=True)
    result.write_text("start\\n" + "x" * 2500 + "\\nfinal test accuracy: 98.99%\\n")
    state.artifacts.append(Artifact(
        id="repro_001", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="tasks/repro/result.md", summary="reproduced successfully",
    ))

    context = build_controller_context(state, model="deepseek-v4-pro")

    assert "98.99%" in context
    assert "Latest Result Evidence" in context
