"""Tests for context.py — context building for controller and adapters."""

from resagent.models import (
    ResearchState, ResearchRun, Artifact, AgentTask, DecisionRecord,
    Observation, ArtifactType, Producer, AgentKind, ActionName,
)
from resagent.context import (
    build_controller_context, build_expagent_context,
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
    def test_expagent_context(self):
        state = _make_state()
        ctx = build_expagent_context(state)
        assert "situation" in ctx
        assert "artifacts" in ctx
        assert "existing_plan" in ctx

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
