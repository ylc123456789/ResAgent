"""Deterministic closure tests for the four-module orchestration path."""

from pathlib import Path

import yaml

from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter, _seed_source_workspace
from resagent.controller import Controller
from resagent.models import (
    ActionName, AgentKind, AgentTask, Artifact, ArtifactType, Producer,
    RunStatus, TaskPriority, TaskStatus,
)
from resagent.planner import PlannedAction
from resagent.state import init_state, load_state, save_state, submit_user_response
from resagent.workspace_layout import WorkspaceLayout
from resagent.task_contracts import allowed_action_candidates


class ScriptedPlanner:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def choose_action(self, state):
        self.calls += 1
        if not self.actions:
            raise AssertionError("scripted planner exhausted")
        return self.actions.pop(0)

    def classify_failure(self, task_id, error):
        return {"category": "unknown"}


def _controller(planner):
    return Controller(
        planner=planner,
        expagent=ExpAgentAdapter(mock=True),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=ReproAgentAdapter(mock=True),
    )


def test_wrong_owner_error_cannot_be_hidden_by_finish(tmp_path):
    state = init_state("wrong-owner", str(tmp_path), "goal")
    state.artifacts.append(Artifact(
        id="decision", type=ArtifactType.scientific_decision,
        producer=Producer.ExpAgent, path="decision.json",
    ))
    task = AgentTask(
        id="task_001", agent=Producer.ExpAgent, kind=AgentKind.advise,
        required=False,
    )
    state.tasks.append(task)
    planner = ScriptedPlanner([
        PlannedAction(ActionName.call_coding_agent, {"task_id": task.id}),
        PlannedAction(ActionName.finish, {"summary": "done"}),
    ])
    ctrl = _controller(planner)

    first = ctrl.step(state)
    second = ctrl.step(state)

    assert first.result == "error"
    assert second.result == "rejected"
    assert state.run.status == RunStatus.running


def test_completed_run_never_calls_planner_again(tmp_path):
    class PanicPlanner:
        def choose_action(self, state):
            raise AssertionError("planner must not run after completion")

    state = init_state("terminal", str(tmp_path), "goal")
    state.run.status = RunStatus.completed
    obs = _controller(PanicPlanner()).step(state)
    assert obs.result == "terminal"


def test_task_bound_ask_user_persists_answers_and_completes_task(tmp_path):
    state = init_state("ask-user", str(tmp_path), "goal")
    task = AgentTask(
        id="task_001", agent=Producer.ResAgent, kind=AgentKind.ask_user,
        capability="request_user_input", input={"question": "Approve?"},
    )
    state.tasks.append(task)
    ctrl = _controller(ScriptedPlanner([
        PlannedAction(ActionName.ask_user, {"task_id": task.id}),
    ]))

    obs = ctrl.step(state)
    save_state(state)
    restored = load_state(str(tmp_path), "ask-user")

    assert obs.result == "user_response_required"
    assert restored is not None
    assert restored.pending_question is not None
    assert restored.run.status == RunStatus.paused
    submit_user_response(
        restored, restored.pending_question.question_id, "yes",
    )
    assert restored.find_task(task.id).status == TaskStatus.completed
    assert restored.run.status == RunStatus.running


def test_codingagent_mock_session_links_to_parent_run(tmp_path):
    layout = WorkspaceLayout(str(tmp_path), "parent-run")
    task = AgentTask(
        id="task_007", agent=Producer.CodingAgent,
        kind=AgentKind.coding_task,
        input={"workspace_path": str(tmp_path / "repo"), "task_goal": "edit"},
    )

    CodingAgentAdapter(mock=True).execute(task, layout, attempt_number=2)
    card_path = layout.codingagent_attempt_dir(7, 2) / "session.yaml"
    card = yaml.safe_load(card_path.read_text(encoding="utf-8"))

    assert card["parent"] == {
        "module": "resagent", "run_id": "parent-run",
        "task_id": "task_007", "attempt": 2,
    }


def test_two_repro_tasks_share_run_environment_namespace(tmp_path, monkeypatch):
    captured = []
    adapter = ReproAgentAdapter(mock=False)

    def fake_call(spec, out_dir, parent_run=None):
        captured.append(parent_run["run_id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.md").write_text("ok", encoding="utf-8")
        return {"status": "completed", "summary": "ok"}, "completed"

    monkeypatch.setattr(adapter, "_call_execute", fake_call)
    layout = WorkspaceLayout(str(tmp_path), "shared-env-run")
    for task_id, epochs in (("task_001", 2), ("task_002", 3)):
        task = AgentTask(
            id=task_id, agent=Producer.ReproAgent, kind=AgentKind.repro_task,
            input={"repo_url": "repo", "experiment_goal": f"{epochs} epochs"},
        )
        adapter.execute(task, layout)

    assert captured == ["shared-env-run", "shared-env-run"]


def test_expagent_deduplicates_equivalent_recommendations(tmp_path):
    state = init_state("dedupe", str(tmp_path), "goal")
    adapter = ExpAgentAdapter(mock=True)
    adapter._state = state
    action = {
        "priority": "high", "type": "repro_task", "rationale": "baseline",
        "plan": {"kind": "repro_task", "paper_url": "p", "repo_url": "r",
                 "experiment_goal": "run baseline"},
    }
    first = adapter._actions_to_tasks([action], "decision_1", 1)
    state.tasks.extend(first)
    second = adapter._actions_to_tasks([action], "decision_2", 2)
    assert len(first) == 1
    assert second == []


def test_followup_run_task_becomes_second_repro_task(tmp_path):
    state = init_state("followup", str(tmp_path), "goal")
    first = AgentTask(
        id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        status=TaskStatus.completed, priority=TaskPriority.high,
        input={"paper_url": "p", "repo_url": "r",
               "experiment_goal": "2 epochs"},
    )
    state.tasks.append(first)
    adapter = ExpAgentAdapter(mock=True)
    adapter._state = state
    followup = adapter._actions_to_tasks([{
        "priority": "high", "type": "run_task", "rationale": "consistency",
        "plan": {"kind": "run_task", "command_goal": "run 3 epochs"},
    }], "decision_2", 2)

    assert len(followup) == 1
    assert followup[0].agent == Producer.ReproAgent
    assert followup[0].input["repo_url"] == "r"
    assert followup[0].input["experiment_goal"] == "run 3 epochs"


def test_same_decision_dependency_chain_routes_and_orders_tasks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('original')\n", encoding="utf-8")
    state = init_state("dependency-chain", str(tmp_path), "goal")
    adapter = ExpAgentAdapter(mock=True)
    adapter._state = state
    actions = [
        {
            "priority": "high", "type": "coding_task",
            "action_id": "patch", "project_ref": "project",
            "rationale": "patch",
            "plan": {"kind": "coding_task", "workspace_path": str(repo),
                     "task_goal": "change training"},
        },
        {
            "priority": "high", "type": "run_task",
            "action_id": "run", "depends_on": ["patch"],
            "project_ref": "project", "rationale": "verify",
            "plan": {"kind": "run_task", "command_goal": "run one epoch"},
        },
    ]
    tasks = adapter._actions_to_tasks(actions, "decision", 1)
    state.tasks.extend(tasks)
    assert [task.agent for task in tasks] == [Producer.CodingAgent, Producer.ReproAgent]
    assert tasks[1].depends_on == [tasks[0].id]
    assert tasks[1].input["source_workspace"] == str(repo)
    assert {"action": "call_coding_agent", "task_id": tasks[0].id} in allowed_action_candidates(state)
    assert {"action": "call_repro_agent", "task_id": tasks[1].id} not in allowed_action_candidates(state)
    tasks[0].status = TaskStatus.completed
    assert {"action": "call_repro_agent", "task_id": tasks[1].id} in allowed_action_candidates(state)


def test_dependent_action_does_not_need_its_own_action_id(tmp_path):
    """An action needs an ID only when another action references it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    state = init_state("optional-dependent-id", str(tmp_path), "goal")
    adapter = ExpAgentAdapter(mock=True)
    adapter._state = state
    tasks = adapter._actions_to_tasks([
        {
            "type": "coding_task", "action_id": "patch",
            "rationale": "patch", "plan": {
                "kind": "coding_task", "workspace_path": str(repo),
                "task_goal": "change code",
            },
        },
        {
            "type": "run_task", "depends_on": ["patch"],
            "rationale": "verify", "plan": {
                "kind": "run_task", "command_goal": "run once",
            },
        },
    ], "decision", 1)

    assert adapter._normalization_issues == []
    assert len(tasks) == 2
    assert tasks[1].action_id == ""
    assert tasks[1].depends_on == [tasks[0].id]


def test_dependency_cycle_is_rejected_atomically(tmp_path):
    state = init_state("dependency-cycle", str(tmp_path), "goal")
    adapter = ExpAgentAdapter(mock=True)
    adapter._state = state
    tasks = adapter._actions_to_tasks([
        {"type": "coding_task", "action_id": "a", "depends_on": ["b"],
         "rationale": "a", "plan": {"kind": "coding_task", "task_goal": "a"}},
        {"type": "coding_task", "action_id": "b", "depends_on": ["a"],
         "rationale": "b", "plan": {"kind": "coding_task", "task_goal": "b"}},
    ], "decision", 1)
    assert tasks == []
    assert any("cycle" in issue for issue in adapter._normalization_issues)


def test_direct_dispatch_cannot_bypass_dependencies(tmp_path):
    state = init_state("dependency-guard", str(tmp_path), "goal")
    prerequisite = AgentTask(id="task_001", agent=Producer.CodingAgent,
        kind=AgentKind.coding_task)
    dependent = AgentTask(id="task_002", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, depends_on=[prerequisite.id])
    state.tasks.extend([prerequisite, dependent])
    ctrl = _controller(ScriptedPlanner([
        PlannedAction(ActionName.call_repro_agent, {"task_id": dependent.id}),
    ]))
    observation = ctrl.step(state)
    assert observation.result == "error"
    assert "waiting for dependencies" in observation.detail


def test_source_workspace_snapshot_preserves_uncommitted_edits(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "run" / "repo"
    source.mkdir()
    (source / "train.py").write_text("patched = True\n", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "ignored.pyc").write_bytes(b"cache")
    _seed_source_workspace(str(source), destination)
    assert (destination / "train.py").read_text(encoding="utf-8") == "patched = True\n"
    assert not (destination / "__pycache__").exists()
