from pathlib import Path

import yaml

from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from tests.v2_registry import make_registry
from resagent.adapters.expagent.task_conversion import actions_to_tasks
from resagent.adapters.reproagent import ReproAgentAdapter
from resagent.controller import Controller
from resagent.models import (
    ActionName,
    AgentKind,
    AgentTask,
    Artifact,
    ArtifactType,
    Producer,
    ResourceRef,
    TaskStatus,
)
from resagent.controller.planner import PlannedAction
from resagent.resources import (
    materialize_task_bindings,
    materialize_dependency_artifacts,
    register_task_resources,
    resolve_artifact_path,
    schedule_coding_repair,
)
from resagent.persistence.state import init_state
from resagent.persistence.workspace import WorkspaceLayout


class ScriptedPlanner:
    def __init__(self, actions):
        self.actions = list(actions)

    def choose_action(self, _state):
        return self.actions.pop(0)

    def classify_failure(self, _task_id, _error):
        return {"category": "unknown"}


def test_logical_chain_resolves_same_repo_and_environment(tmp_path):
    state = init_state("chain", str(tmp_path), "prepare, patch, run")
    state.resources.extend([
        ResourceRef(
            kind="repo", id="project", path=str(tmp_path / "repo"),
            created_by=Producer.ReproAgent, created_task="task_001",
        ),
        ResourceRef(
            kind="environment", id="env-chain", repo="project",
            certification="experiment", created_by=Producer.ReproAgent,
            created_task="task_001",
        ),
    ])
    coding = AgentTask(
        id="task_002", agent=Producer.CodingAgent,
        kind=AgentKind.coding_task, project_ref="project",
        input={"task_goal": "patch", "workspace_intent": "shared"},
    )
    experiment = AgentTask(
        id="task_003", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, project_ref="project",
        depends_on=[coding.id],
        input={"experiment_goal": "run", "workspace_intent": "shared"},
    )

    layout = WorkspaceLayout(str(tmp_path), "chain")
    materialize_task_bindings(state, coding, layout)
    materialize_task_bindings(state, experiment, layout)

    assert coding.input["workspace_path"] == str(tmp_path / "repo")
    assert coding.input["env_name"] == "env-chain"
    assert coding.input["env_policy"] == "reuse_only"
    assert experiment.input["external_repo_path"] == str(tmp_path / "repo")
    assert experiment.input["allow_code_delegation"] is False


def test_dependency_repo_supersedes_stale_initial_locator(tmp_path):
    state = init_state("snapshot", str(tmp_path), "patch then run")
    patched_repo = tmp_path / "patched"
    state.resources.append(ResourceRef(
        kind="repo", id="project", path=str(patched_repo),
        created_by=Producer.CodingAgent, created_task="task_001",
    ))
    experiment = AgentTask(
        id="task_002", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, project_ref="project",
        depends_on=["task_001"],
        input={
            "repo_url": "/original/repo",
            "workspace_intent": "isolated",
            "experiment_goal": "verify patch",
        },
    )

    materialize_task_bindings(
        state, experiment, WorkspaceLayout(str(tmp_path), "snapshot"),
    )

    assert experiment.input["repo_url"] == ""
    assert experiment.input["copy_from"] == str(patched_repo)
    assert experiment.input.get("external_repo_path", "") == ""


def test_reproduce_patch_run_graph_routes_three_tasks(tmp_path):
    state = init_state("graph", str(tmp_path), "reproduce, patch and run")
    tasks, issues = actions_to_tasks([
        {
            "action_id": "setup", "capability": "reproduce_experiment",
            "objective": "prepare repo", "rationale": "setup",
            "depends_on": [], "project_ref": "project", "required": True,
            "repo_url": "https://example/repo.git", "paper_url": "",
            "expected_metrics": [],
        },
        {
            "action_id": "patch", "capability": "modify_code",
            "objective": "implement method", "rationale": "implement",
            "depends_on": ["setup"], "project_ref": "project", "required": True,
            "constraints": [], "verify_commands": [], "expected_artifacts": [],
        },
        {
            "action_id": "run", "capability": "execute_experiment",
            "objective": "run experiment", "rationale": "measure",
            "depends_on": ["patch"], "project_ref": "project", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
    ], state, "decision", 1, registry=make_registry())

    assert issues == []
    assert [task.agent for task in tasks] == [
        Producer.ReproAgent, Producer.CodingAgent, Producer.ReproAgent,
    ]
    setup, coding, experiment = tasks
    assert setup.input["repo_url"] == "https://example/repo.git"
    assert coding.depends_on == [setup.id]
    assert experiment.depends_on == [coding.id]


def test_reproduce_experiment_carries_action_repo(tmp_path):
    """In V2 the repo locator comes from the action, not ResAgent guessing."""
    state = init_state("goal-repo", str(tmp_path), "Use some goal text")
    tasks, issues = actions_to_tasks([
        {
            "action_id": "setup", "capability": "reproduce_experiment",
            "objective": "prepare repo", "rationale": "instrument",
            "depends_on": [], "project_ref": "project", "required": True,
            "repo_url": "https://github.com/org/project.git", "paper_url": "",
            "expected_metrics": [],
        },
    ], state, "decision", 1, registry=make_registry())

    assert issues == []
    assert [task.agent for task in tasks] == [Producer.ReproAgent]
    assert tasks[0].input["repo_url"] == "https://github.com/org/project.git"


def test_goal_local_repo_routes_execute_experiment(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    state = init_state(
        "local-runs", str(tmp_path), f"Using local repository {repo}, run twice",
    )
    tasks, issues = actions_to_tasks([
        {
            "action_id": "first", "capability": "execute_experiment",
            "objective": "run first", "rationale": "",
            "depends_on": [], "project_ref": "project", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
        {
            "action_id": "second", "capability": "execute_experiment",
            "objective": "run second", "rationale": "",
            "depends_on": ["first"], "project_ref": "project", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
    ], state, "decision", 1, registry=make_registry())

    assert issues == []
    assert len(tasks) == 2
    assert tasks[0].input["workspace_path"] == str(repo)
    assert tasks[1].depends_on == [tasks[0].id]

    state.tasks.extend(tasks)
    materialize_task_bindings(
        state, tasks[0], WorkspaceLayout(str(tmp_path), "local-runs"),
    )
    assert tasks[0].input["external_repo_path"] == str(repo)
    assert not tasks[0].input["repo_url"]


def test_ambiguous_goal_yields_empty_workspace(tmp_path):
    """V2 never guesses a repo; ambiguity leaves workspace empty for materialize."""
    state = init_state(
        "ambiguous", str(tmp_path),
        "Compare https://github.com/a/one.git and https://github.com/b/two.git",
    )
    tasks, issues = actions_to_tasks([
        {
            "action_id": "run", "capability": "execute_experiment",
            "objective": "run", "rationale": "", "depends_on": [],
            "project_ref": "project", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
    ], state, "decision", 1, registry=make_registry())
    assert issues == []
    assert tasks[0].input["workspace_path"] == ""


def test_reproduce_patch_experiment_chain_reuses_registered_resources(tmp_path):
    state = init_state("e2e", str(tmp_path), "reproduce, patch and run")
    tasks, issues = actions_to_tasks([
        {
            "action_id": "setup", "capability": "reproduce_experiment",
            "objective": "prepare repo", "rationale": "setup",
            "depends_on": [], "project_ref": "project", "required": True,
            "repo_url": "https://example/repo.git", "paper_url": "",
            "expected_metrics": [],
        },
        {
            "action_id": "patch", "capability": "modify_code",
            "objective": "implement method", "rationale": "implement",
            "depends_on": ["setup"], "project_ref": "project", "required": True,
            "constraints": [], "verify_commands": [], "expected_artifacts": [],
        },
        {
            "action_id": "run", "capability": "execute_experiment",
            "objective": "run experiment", "rationale": "measure",
            "depends_on": ["patch"], "project_ref": "project", "required": True,
            "expected_metrics": [], "requires_gpu": False,
        },
    ], state, "decision", 1, registry=make_registry())
    assert issues == []
    state.tasks.extend(tasks)
    setup, coding, experiment = tasks
    controller = Controller(
        planner=ScriptedPlanner([
            PlannedAction(ActionName.call_repro_agent, {"task_id": setup.id}),
            PlannedAction(ActionName.call_coding_agent, {"task_id": coding.id}),
            PlannedAction(ActionName.call_repro_agent, {"task_id": experiment.id}),
        ]),
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=ReproAgentAdapter(mock=True),
    )

    assert controller.step(state).result == "ok"
    assert {resource.kind for resource in state.resources} == {"repo", "environment"}
    repo = next(resource for resource in state.resources if resource.kind == "repo")
    environment = next(resource for resource in state.resources if resource.kind == "environment")

    assert controller.step(state).result == "ok"
    assert coding.input["workspace_path"] == repo.path
    assert coding.input["env_name"] == environment.id

    assert controller.step(state).result == "ok"
    assert experiment.input["external_repo_path"] == repo.path


def test_never_policy_copies_instead_of_sharing(tmp_path):
    state = init_state("isolated", str(tmp_path), "run")
    state.resources.append(ResourceRef(
        kind="repo", id="project", path=str(tmp_path / "repo"),
        created_by=Producer.CodingAgent, created_task="task_001",
    ))
    task = AgentTask(
        id="task_002", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, project_ref="project",
        input={"experiment_goal": "run", "workspace_intent": "shared"},
    )
    materialize_task_bindings(
        state, task, WorkspaceLayout(str(tmp_path), "isolated"), "never",
    )
    assert task.input["copy_from"] == str(tmp_path / "repo")
    assert not task.input.get("external_repo_path")


def test_current_session_bindings_register_resources(tmp_path):
    state = init_state("bindings", str(tmp_path), "goal")
    task = AgentTask(
        id="task_001", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, project_ref="project",
    )
    card = tmp_path / "session.yaml"
    card.write_text(yaml.safe_dump({"bindings": {
        "repo": {
            "path": "/repo", "origin": "url", "commit": "abc",
            "mode": "isolated",
        },
        "environment": {
            "name": "env-a", "policy": "auto",
            "certification": "experiment", "audit_artifact": "audit.json",
        },
    }}), encoding="utf-8")

    register_task_resources(state, task, str(card))

    assert [(resource.kind, resource.id) for resource in state.resources] == [
        ("repo", "project"), ("environment", "env-a"),
    ]
    assert state.resources[1].repo == "project"


class RepairableRepro:
    def __init__(self):
        self.calls = 0

    def execute(self, task, _layout, attempt_number=1):
        self.calls += 1
        if self.calls > 1:
            return {
                "artifact": Artifact(
                    id="repro_retried", type=ArtifactType.repro_result,
                    producer=Producer.ReproAgent, path="result.md",
                ),
                "outcome": "completed",
                "raw": {"summary": "experiment completed after repair"},
                "workspace_path": task.input.get("external_repo_path", ""),
            }
        return {
            "artifact": Artifact(
                id="repro", type=ArtifactType.repro_result,
                producer=Producer.ReproAgent, path="result.md",
            ),
            "outcome": "blocked",
            "raw": {"summary": "needs instrumentation"},
            "workspace_path": task.input.get("external_repo_path", ""),
            "coding_issues": ["add loss logging"],
        }


class WarningRepro:
    def execute(self, task, _layout, attempt_number=1):
        return {
            "artifact": Artifact(
                id="repro_warning", type=ArtifactType.repro_result,
                producer=Producer.ReproAgent, path="result.md",
            ),
            "outcome": "completed_with_warnings",
            "raw": {"summary": "metrics recovered; one optional plot is missing"},
        }


def test_completed_with_warnings_is_not_stored_as_an_error(tmp_path):
    state = init_state("warning", str(tmp_path), "run")
    task = AgentTask(
        id="task_001", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, input={"experiment_goal": "run"},
    )
    state.tasks.append(task)
    controller = Controller(
        planner=ScriptedPlanner([
            PlannedAction(ActionName.call_repro_agent, {"task_id": task.id}),
        ]),
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=WarningRepro(),
    )

    assert controller.step(state).result == "ok"
    assert task.status == TaskStatus.completed
    assert task.error == ""
    assert task.warnings == ["metrics recovered; one optional plot is missing"]


def test_blocked_operator_routes_repair_and_resumes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = init_state("repair", str(tmp_path), "run")
    state.resources.append(ResourceRef(
        kind="repo", id="project", path=str(repo),
        created_by=Producer.ReproAgent, created_task="setup",
    ))
    state.resources.append(ResourceRef(
        kind="environment", id="env-repair", repo="project",
        certification="experiment", created_by=Producer.ReproAgent,
        created_task="setup",
    ))
    repro = AgentTask(
        id="task_001", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, project_ref="project",
        input={"experiment_goal": "run", "workspace_intent": "shared"},
    )
    state.tasks.append(repro)
    operator = RepairableRepro()
    controller = Controller(
        planner=ScriptedPlanner([
            PlannedAction(ActionName.call_repro_agent, {"task_id": repro.id}),
            PlannedAction(ActionName.call_coding_agent, {"task_id": "task_002"}),
            PlannedAction(ActionName.call_repro_agent, {"task_id": repro.id}),
        ]),
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=operator,
    )

    blocked_observation = controller.step(state)
    repair = state.tasks[-1]

    assert blocked_observation.result == "ok"
    assert "scheduled CodingAgent repair task_002" in blocked_observation.detail
    assert blocked_observation.task_ids == [repro.id, repair.id]
    assert repro.status == TaskStatus.blocked
    assert repair.agent == Producer.CodingAgent
    assert repair.input["workspace_path"] == str(repo)
    assert repair.input["env_policy"] == "frozen"
    assert repair.input["env_name"] == "env-repair"
    assert repro.depends_on == [repair.id]

    repair_observation = controller.step(state)
    assert repair_observation.result == "ok"
    assert repair.status == TaskStatus.completed
    assert repro.status == TaskStatus.pending
    assert "_repair_task_id" not in repro.input

    retry_observation = controller.step(state)
    assert retry_observation.result == "ok"
    assert repro.status == TaskStatus.completed
    assert len(repro.attempts) == 2
    assert operator.calls == 2


def test_blocked_operator_without_repair_context_remains_error(tmp_path):
    """A blocker is still fatal when no repo/environment can host a repair."""
    state = init_state("unroutable-repair", str(tmp_path), "run")
    repro = AgentTask(
        id="task_001", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, input={"experiment_goal": "run"},
    )
    state.tasks.append(repro)
    controller = Controller(
        planner=ScriptedPlanner([
            PlannedAction(ActionName.call_repro_agent, {"task_id": repro.id}),
        ]),
        expagent=ExpAgentAdapter(mock=True, registry=make_registry()),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=RepairableRepro(),
    )

    observation = controller.step(state)

    assert observation.result == "error"
    assert repro.status == TaskStatus.blocked
    assert len(state.tasks) == 1


def test_operator_repair_routing_is_bounded(tmp_path):
    state = init_state("bounded", str(tmp_path), "run")
    state.budget.max_task_retries = 1
    state.resources.extend([
        ResourceRef(kind="repo", id="project", path=str(tmp_path / "repo"),
                    created_by=Producer.ReproAgent, created_task="setup"),
        ResourceRef(kind="environment", id="env", repo="project",
                    created_by=Producer.ReproAgent, created_task="setup"),
    ])
    repro = AgentTask(id="task_001", agent=Producer.ReproAgent,
                      kind=AgentKind.repro_task, project_ref="project")
    state.tasks.append(repro)
    first = schedule_coding_repair(state, repro, ["fix"], str(tmp_path / "repo"))
    assert first is not None
    repro.input.pop("_repair_task_id", None)
    assert schedule_coding_repair(
        state, repro, ["fix again"], str(tmp_path / "repo"),
    ) is None


def test_multiple_dependency_artifacts_are_bound_with_actual_paths(tmp_path):
    state = init_state("fan-in", str(tmp_path), "compare experiments")
    run_root = tmp_path / "fan-in"
    first_path = run_root / "tasks" / "first" / "result.md"
    second_path = run_root / "tasks" / "second" / "result.md"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_text("accuracy=0.8", encoding="utf-8")
    second_path.write_text("accuracy=0.9", encoding="utf-8")
    first = AgentTask(
        id="task_001", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, status=TaskStatus.completed,
        artifacts=["result_1"],
    )
    second = AgentTask(
        id="task_002", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, status=TaskStatus.completed,
        artifacts=["result_2"],
    )
    analysis = AgentTask(
        id="task_003", agent=Producer.ExpAgent, kind=AgentKind.advise,
        depends_on=[first.id, second.id],
    )
    state.tasks.extend([first, second, analysis])
    state.artifacts.extend([
        Artifact(
            id="result_1", type=ArtifactType.repro_result,
            producer=Producer.ReproAgent,
            path=str(first_path.relative_to(run_root)), summary="first",
        ),
        Artifact(
            id="result_2", type=ArtifactType.repro_result,
            producer=Producer.ReproAgent,
            path=str(second_path.relative_to(run_root)), summary="second",
        ),
    ])

    bindings = materialize_dependency_artifacts(state, analysis)

    assert [item["producer_task_id"] for item in bindings] == [
        "task_001", "task_002",
    ]
    assert [item["path"] for item in bindings] == [
        str(first_path.resolve()), str(second_path.resolve()),
    ]


def test_artifact_path_resolution_accepts_run_relative_and_absolute_paths(tmp_path):
    state = init_state("paths", str(tmp_path), "resolve artifacts")
    relative = Path("tasks/repro/result.md")
    absolute = tmp_path / "external" / "result.md"

    assert resolve_artifact_path(state, relative) == (
        tmp_path / "paths" / relative
    ).resolve()
    assert resolve_artifact_path(state, absolute) == absolute.resolve()
