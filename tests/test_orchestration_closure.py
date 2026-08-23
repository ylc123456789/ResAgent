"""Deterministic closure tests for the four-module orchestration path."""

from pathlib import Path

import pytest
import yaml

from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter
from resagent.controller import Controller
from resagent.models import (
    ActionName, AgentKind, AgentTask, Artifact, ArtifactType, Producer,
    RunStatus, TaskPriority, TaskStatus,
)
from resagent.controller.planner import PlannedAction, Planner, PlannerError
from resagent.persistence.state import init_state, load_state, save_state, submit_user_response
from resagent.persistence.workspace import WorkspaceLayout
from resagent.controller.contracts import allowed_action_candidates
from tests.v2_registry import make_registry


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
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
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


def _state_with_acceptance_issue(tmp_path):
    state = init_state("acceptance", str(tmp_path), "goal")
    task = AgentTask(
        id="task_001", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, capability="execute_experiment",
        status=TaskStatus.completed, required=True, analysis_required=False,
        artifacts=["result"],
    )
    state.tasks.append(task)
    state.artifacts.append(Artifact(
        id="result", type=ArtifactType.repro_result,
        producer=Producer.ReproAgent, path="result.json",
        metadata={
            "outcome": "completed_with_warnings",
            "raw_result": {
                "structured_result": {
                    "delivery": {"issues": ["Missing required metric: accuracy"]},
                },
            },
        },
    ))
    return state


def test_final_acceptance_issue_pauses_without_calling_planner(tmp_path):
    class PanicPlanner:
        def choose_action(self, state):
            raise AssertionError("final acceptance gate must be deterministic")

    state = _state_with_acceptance_issue(tmp_path)
    obs = _controller(PanicPlanner()).step(state)

    assert obs.result == "user_response_required"
    assert state.run.status == RunStatus.paused
    assert state.pending_question is not None
    assert state.pending_question.requested_fields == ["final_acceptance_decision"]
    assert "Missing required metric: accuracy" in state.pending_question.text


def test_user_can_accept_final_issues_and_finish(tmp_path):
    class PanicPlanner:
        def choose_action(self, state):
            raise AssertionError("explicit finish must not call the planner")

    state = _state_with_acceptance_issue(tmp_path)
    controller = _controller(PanicPlanner())
    controller.step(state)
    submit_user_response(
        state, state.pending_question.question_id, "接受当前结果并收尾",
    )

    obs = controller.step(state)

    assert obs.result == "ok"
    assert state.run.status == RunStatus.completed


def test_fix_answer_replans_through_expagent(tmp_path):
    state = _state_with_acceptance_issue(tmp_path)
    controller = _controller(ScriptedPlanner([
        PlannedAction(ActionName.call_exp_agent, {"task_id": "task_002"}),
    ]))
    controller.step(state)
    submit_user_response(state, state.pending_question.question_id, "修复这些问题")

    directive = state.user_directives[-1]
    assert directive.kind.value == "plan_revision"
    assert directive.handled is False

    obs = controller.step(state)

    assert obs.result == "ok"
    assert directive.handled is True
    assert state.find_task("task_002").agent == Producer.ExpAgent
    assert state.find_task("task_002").status == TaskStatus.completed


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
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state
    action = {
        "action_id": "repro", "capability": "reproduce_experiment",
        "objective": "run baseline", "rationale": "baseline",
        "depends_on": [], "project_ref": "project", "required": True,
        "paper_url": "p", "repo_url": "r", "expected_metrics": [],
    }
    first = adapter._actions_to_tasks([action], "decision_1", 1)
    state.tasks.extend(first)
    second = adapter._actions_to_tasks([action], "decision_2", 2)
    assert len(first) == 1
    assert second == []


def test_expagent_adapter_applies_only_explicit_supersedes(tmp_path):
    state = init_state("explicit-replace", str(tmp_path), "goal")
    old = AgentTask(
        id="task_001", source="decision_1", action_id="old_run",
        agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        priority=TaskPriority.medium, input={},
    )
    keep = AgentTask(
        id="task_002", source="decision_1", action_id="keep_run",
        agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        priority=TaskPriority.medium, input={},
    )
    state.tasks.extend([old, keep])
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state

    new_tasks = adapter._actions_to_tasks(
        [{
            "action_id": "new_run", "capability": "execute_experiment",
            "objective": "run revised experiment", "rationale": "revised plan",
            "depends_on": [], "project_ref": "project", "required": True,
            "expected_metrics": ["accuracy"], "requires_gpu": False,
        }],
        "decision_2",
        3,
        supersedes_action_ids=["old_run"],
    )

    assert len(new_tasks) == 1
    assert old.status == TaskStatus.skipped
    assert keep.status == TaskStatus.pending


def test_followup_experiment_inherits_workspace_from_prior_repro(tmp_path):
    state = init_state("followup", str(tmp_path), "goal")
    first = AgentTask(
        id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        status=TaskStatus.completed, priority=TaskPriority.high,
        input={"paper_url": "p", "repo_url": "r",
               "experiment_goal": "2 epochs", "workspace_path": "/prior/repo"},
    )
    state.tasks.append(first)
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state
    followup = adapter._actions_to_tasks([{
        "action_id": "run_more", "capability": "execute_experiment",
        "objective": "run 3 epochs", "rationale": "consistency",
        "depends_on": [], "project_ref": "project", "required": True,
        "expected_metrics": [], "requires_gpu": False,
    }], "decision_2", 2)

    assert len(followup) == 1
    assert followup[0].agent == Producer.ReproAgent
    assert followup[0].input["workspace_path"] == "/prior/repo"
    assert followup[0].input["experiment_goal"] == "run 3 epochs"


def test_same_decision_dependency_chain_routes_and_orders_tasks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('original')\n", encoding="utf-8")
    state = init_state("dependency-chain", str(tmp_path), f"Modify and run {repo}")
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state
    actions = [
        {
            "action_id": "patch", "capability": "modify_code",
            "objective": "change training", "rationale": "patch",
            "depends_on": [], "project_ref": "project", "required": True,
            "constraints": [], "verify_commands": [], "expected_artifacts": [],
        },
        {
            "action_id": "run", "capability": "execute_experiment",
            "objective": "run one epoch", "rationale": "verify",
            "depends_on": ["patch"], "project_ref": "project", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
    ]
    tasks = adapter._actions_to_tasks(actions, "decision", 1)
    state.tasks.extend(tasks)
    assert [task.agent for task in tasks] == [Producer.CodingAgent, Producer.ReproAgent]
    assert tasks[1].depends_on == [tasks[0].id]
    assert tasks[1].input["workspace_path"] == str(repo)
    assert {"action": "call_coding_agent", "task_id": tasks[0].id} in allowed_action_candidates(state)
    assert {"action": "call_repro_agent", "task_id": tasks[1].id} not in allowed_action_candidates(state)
    tasks[0].status = TaskStatus.completed
    assert {"action": "call_repro_agent", "task_id": tasks[1].id} in allowed_action_candidates(state)


def test_every_action_requires_its_own_action_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = init_state("optional-dependent-id", str(tmp_path), "goal")
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state
    tasks = adapter._actions_to_tasks([
        {
            "action_id": "patch", "capability": "modify_code",
            "objective": "change code", "rationale": "patch",
            "depends_on": [], "project_ref": "", "required": True,
            "constraints": [], "verify_commands": [], "expected_artifacts": [],
        },
        {
            "capability": "execute_experiment",
            "objective": "run once", "rationale": "verify",
            "depends_on": ["patch"], "project_ref": "", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
    ], "decision", 1)

    assert tasks == []
    assert any("non-empty action_id" in issue
               for issue in adapter._normalization_issues)


def test_dependent_run_inherits_workspace_inferred_for_prerequisite(tmp_path):
    """Use the normalized prerequisite task, not only ExpAgent's raw plan."""
    repo = tmp_path / "fixture"
    repo.mkdir()
    script = repo / "train.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    state = init_state(
        "inferred-workspace", str(tmp_path),
        f"Modify {script} and then run it",
    )
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state
    tasks = adapter._actions_to_tasks([
        {
            "action_id": "patch", "capability": "modify_code",
            "objective": "change code", "rationale": "patch",
            "depends_on": [], "project_ref": "fixture", "required": True,
            "constraints": [], "verify_commands": [], "expected_artifacts": [],
        },
        {
            "action_id": "run", "capability": "execute_experiment",
            "objective": "run once", "rationale": "verify",
            "depends_on": ["patch"], "project_ref": "fixture", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
    ], "decision", 1)

    assert adapter._normalization_issues == []
    assert len(tasks) == 2
    assert tasks[0].input["workspace_path"] == str(repo)
    assert tasks[1].input["workspace_path"] == str(repo)


def test_dependency_cycle_is_rejected_atomically(tmp_path):
    state = init_state("dependency-cycle", str(tmp_path), "goal")
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state
    tasks = adapter._actions_to_tasks([
        {"action_id": "a", "capability": "modify_code", "objective": "a",
         "rationale": "a", "depends_on": ["b"], "project_ref": "",
         "required": True, "constraints": [], "verify_commands": [],
         "expected_artifacts": []},
        {"action_id": "b", "capability": "modify_code", "objective": "b",
         "rationale": "b", "depends_on": ["a"], "project_ref": "",
         "required": True, "constraints": [], "verify_commands": [],
         "expected_artifacts": []},
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


def test_completed_repro_workspace_is_available_to_followup_coding(tmp_path):
    repo = tmp_path / "runs" / "workspace-owner" / "tasks" / "reproagent" / "repo"
    repo.mkdir(parents=True)

    class MaterializingReproAgent:
        def execute(self, task, layout, attempt_number=1):
            artifact_path = tmp_path / "result.md"
            artifact_path.write_text("ok", encoding="utf-8")
            return {
                "artifact": Artifact(
                    id="repro_result_1",
                    type=ArtifactType.repro_result,
                    producer=Producer.ReproAgent,
                    path=str(artifact_path),
                    summary="ok",
                ),
                "outcome": "completed",
                "raw": {"summary": "ok"},
                "workspace_path": str(repo),
            }

    state = init_state("workspace-owner", str(tmp_path), "reproduce then patch")
    repro_task = AgentTask(
        id="task_001",
        agent=Producer.ReproAgent,
        kind=AgentKind.repro_task,
        input={"repo_url": "https://example.invalid/repo.git"},
    )
    state.tasks.append(repro_task)
    controller = Controller(
        planner=ScriptedPlanner([
            PlannedAction(ActionName.call_repro_agent, {"task_id": repro_task.id}),
        ]),
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=MaterializingReproAgent(),
    )

    observation = controller.step(state)
    assert observation.result == "ok"
    assert repro_task.input["workspace_path"] == str(repo)

    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state
    followup = adapter._actions_to_tasks([{
        "action_id": "instrument", "capability": "modify_code",
        "objective": "add metrics", "rationale": "instrument the reproduced code",
        "depends_on": [], "project_ref": "", "required": True,
        "constraints": [], "verify_commands": [], "expected_artifacts": [],
    }], "decision_002", state.next_task_number())
    assert len(followup) == 1
    assert followup[0].input["workspace_path"] == str(repo)


def test_retry_replaces_stale_attempt_artifact(tmp_path):
    """A retried task's artifact replaces the stale one under the same id.

    Regression (cloud v2-regression-20260814): attempt 1 blocked and its
    report was registered as repro_result_001; attempt 2 succeeded and
    registered the SAME task-derived id. With both entries in the registry,
    find_artifact resolved to the blocked report and the analysis concluded
    no usable result existed, forcing a redundant re-run. The registry must
    hold exactly the latest artifact for the id.
    """
    class FlakyReproAgent:
        def execute(self, task, layout, attempt_number=1):
            if attempt_number == 1:
                text, outcome = "Status: blocked — code changes required", "blocked"
            else:
                text, outcome = "Status: completed — test acc 0.99", "completed"
            path = tmp_path / f"result_att{attempt_number}.md"
            path.write_text(text, encoding="utf-8")
            return {
                "artifact": Artifact(
                    id="repro_result_001",
                    type=ArtifactType.repro_result,
                    producer=Producer.ReproAgent,
                    path=str(path),
                    summary=text[:200],
                ),
                "outcome": outcome,
                "raw": {"summary": outcome},
                "workspace_path": "",
            }

    state = init_state("retry-artifacts", str(tmp_path), "run the experiment")
    task = AgentTask(
        id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        capability="execute_experiment", input={"objective": "run it"},
    )
    state.tasks.append(task)
    ctrl = Controller(
        planner=ScriptedPlanner([
            PlannedAction(ActionName.call_repro_agent, {"task_id": "task_001"}),
            PlannedAction(ActionName.call_repro_agent, {"task_id": "task_001"}),
        ]),
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=FlakyReproAgent(),
    )

    ctrl.step(state)  # attempt 1: blocked
    ctrl.step(state)  # attempt 2: completed (post-repair retry)

    assert task.status == TaskStatus.completed
    matching = [a for a in state.artifacts if a.id == "repro_result_001"]
    assert len(matching) == 1, "stale attempt artifact must be replaced"
    current = state.find_artifact("repro_result_001")
    assert current.path.endswith("result_att2.md")
    assert "completed" in current.summary
    assert task.artifacts == ["repro_result_001"]


def test_artifact_numbering_is_stable_after_replacement(tmp_path):
    """next_artifact_number is max-based, immune to register replacements."""
    state = init_state("artifact-numbering", str(tmp_path), "numbering")
    state.register_artifact(Artifact(
        id="exp_decision_001", type=ArtifactType.scientific_decision,
        producer=Producer.ExpAgent, path="d1",
    ))
    state.register_artifact(Artifact(
        id="repro_result_002", type=ArtifactType.repro_result,
        producer=Producer.ReproAgent, path="r2",
    ))
    assert state.next_artifact_number() == 3
    # Replacement shrinks the list; numbering must not reuse an existing id.
    state.register_artifact(Artifact(
        id="repro_result_002", type=ArtifactType.repro_result,
        producer=Producer.ReproAgent, path="r2-new",
    ))
    assert len(state.artifacts) == 2
    assert state.next_artifact_number() == 3


def test_required_action_cannot_depend_on_optional():
    """Ingest rejects the scheduler trap: required depending on optional.

    Requirement flows backward along hard dependencies — an optional chain
    stays optional end-to-end, a required chain is required end-to-end.
    """
    from resagent.adapters.expagent.dependency_graph import dependency_graph_issues

    trap = [
        {"action_id": "exp", "capability": "execute_experiment",
         "required": False, "depends_on": []},
        {"action_id": "ana", "capability": "analyze_results",
         "required": True, "depends_on": ["exp"]},
    ]
    issues = dependency_graph_issues(trap)
    assert any("required" in i and "optional" in i for i in issues), issues

    for exp_required, ana_required in [(True, True), (False, False)]:
        chain = [
            {"action_id": "exp", "capability": "execute_experiment",
             "required": exp_required, "depends_on": []},
            {"action_id": "ana", "capability": "analyze_results",
             "required": ana_required, "depends_on": ["exp"]},
        ]
        assert dependency_graph_issues(chain) == []


def test_planner_retries_transient_llm_failures(tmp_path, monkeypatch):
    """A transient empty/unparseable LLM response is retried, not fatal."""
    planner = Planner(mock=False)
    calls = {"n": 0}
    good = (
        '{"analysis":"a", "action":"call_exp_agent", '
        '"params":{"task_id":"task_001"}, "reason":"consult"}'
    )

    def flaky(context):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("empty LLM response (transient gateway behavior)")
        return good

    monkeypatch.setattr(planner, "_call_llm", flaky)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    state = init_state("planner-retry", str(tmp_path), "goal")
    state.tasks.append(AgentTask(
        id="task_001",
        agent=Producer.ExpAgent,
        kind=AgentKind.advise,
        status=TaskStatus.pending,
    ))

    action = planner.choose_action(state)
    assert action.action == ActionName.call_exp_agent
    assert calls["n"] == 3


def test_planner_fails_closed_after_retry_exhaustion(tmp_path, monkeypatch):
    """Persistent LLM outage raises PlannerError (controlled, not a raw crash)."""
    planner = Planner(mock=False)
    monkeypatch.setattr(
        planner, "_call_llm",
        lambda _ctx: (_ for _ in ()).throw(ValueError("empty LLM response")),
    )
    monkeypatch.setattr("time.sleep", lambda _s: None)
    state = init_state("planner-outage", str(tmp_path), "goal")

    with pytest.raises(PlannerError):
        planner.choose_action(state)


def test_planner_rejects_action_outside_runnable_candidates(tmp_path, monkeypatch):
    planner = Planner(mock=False)
    monkeypatch.setattr(
        planner,
        "_call_llm",
        lambda _ctx: (
            '{"analysis":"retry", "action":"call_coding_agent", '
            '"params":{"task_id":"missing"}, "reason":"retry"}'
        ),
    )
    monkeypatch.setattr("time.sleep", lambda _s: None)
    state = init_state("planner-candidates", str(tmp_path), "goal")

    with pytest.raises(PlannerError, match="not currently runnable"):
        planner.choose_action(state)


def test_planner_outage_interrupts_run_without_traceback(tmp_path):
    """The loop turns PlannerError into a resumable interruption, not a crash."""
    class OutagePlanner:
        def choose_action(self, state):
            raise PlannerError("controller LLM unavailable after retries")

        def classify_failure(self, task_id, error):
            return {"category": "unknown"}

    state = init_state("outage-run", str(tmp_path), "goal")
    ctrl = _controller(OutagePlanner())

    result = ctrl.run(state, max_steps=3)

    assert result.run.status == RunStatus.interrupted
    assert "controller LLM" in result.current_summary


def test_p4_scenario_is_repro_then_expagent_then_finish(tmp_path):
    """The P4 flow: one experiment, then one analysis, then a clean finish."""
    state = init_state(
        "p4", str(tmp_path),
        "Run the ODE-Net experiment and analyze deviations from the paper",
    )
    ctrl = _controller(Planner(mock=True))

    result = ctrl.run(state, max_steps=20)

    repro = [t for t in result.tasks if t.agent == Producer.ReproAgent]
    analyze = [t for t in result.tasks if t.capability == "analyze_results"]

    assert result.run.status == RunStatus.completed
    assert len(repro) == 1
    assert repro[0].status == TaskStatus.completed
    assert len(analyze) == 1
    assert analyze[0].status == TaskStatus.completed
    assert analyze[0].depends_on == [repro[0].id]


def test_finish_summary_lists_unexecuted_optional_proposals(tmp_path):
    """Optional follow-ups remain scientific recommendations, not tasks.

    A bounded run must finish when its committed (required) work is done,
    even if the advisor proposed optional next steps (required=False). Those
    proposals stay in the decision artifact and are surfaced at finish.
    """
    state = init_state(
        "prop", str(tmp_path), "Run a bounded experiment and analyze it",
    )
    state.tasks.extend([
        AgentTask(
            id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
            capability="execute_experiment", required=True,
            status=TaskStatus.completed,
            input={"objective": "Run the bounded 3-epoch experiment"},
        ),
        AgentTask(
            id="task_002", agent=Producer.ExpAgent, kind=AgentKind.advise,
            capability="analyze_results", required=True,
            status=TaskStatus.completed, depends_on=["task_001"],
            artifacts=["a2"],
            input={"objective": "Interpret the bounded run"},
        ),
    ])
    state.artifacts.append(Artifact(
        id="a1", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="result.md",
    ))
    state.artifacts.append(Artifact(
        id="a2", type=ArtifactType.scientific_decision,
        producer=Producer.ExpAgent, path="decision.json",
        metadata={"raw_decision": {"recommended_actions": [{
            "action_id": "full_reproduction",
            "capability": "execute_experiment",
            "objective": "Run the full 160-epoch reproduction",
            "required": False,
        }]}},
    ))
    planner = ScriptedPlanner([
        PlannedAction(ActionName.finish, {"summary": "Bounded goal satisfied."}),
    ])
    ctrl = _controller(planner)

    result = ctrl.run(state, max_steps=5)

    assert result.run.status == RunStatus.completed
    assert result.find_task("task_003") is None
    assert "Proposed follow-ups" in result.current_summary
    assert "full_reproduction" in result.current_summary
    assert "160-epoch" in result.current_summary


def test_optional_actions_are_not_materialized_as_tasks(tmp_path):
    state = init_state("optional-record-only", str(tmp_path), "goal")
    adapter = ExpAgentAdapter(mock=True, registry=make_registry())
    adapter._state = state

    tasks = adapter._actions_to_tasks([{
        "action_id": "future_run",
        "capability": "execute_experiment",
        "objective": "Run a future experiment",
        "rationale": "Useful later",
        "depends_on": [],
        "project_ref": "project",
        "required": False,
        "expected_metrics": [],
        "requires_gpu": False,
    }], "decision", 1)

    assert tasks == []
    assert adapter._normalization_issues == []
