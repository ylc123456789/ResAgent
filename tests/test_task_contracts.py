"""Contract tests for task routing and run lifecycle invariants."""

from resagent.models import (
    ActionName, AgentKind, AgentTask, Artifact, ArtifactType, Observation,
    Producer, ResearchRun, ResearchState, RunStatus, TaskStatus,
)
from resagent.task_contracts import (
    allowed_action_candidates, normalize_recommended_action,
    required_from_priority, task_fingerprint, validate_finish,
)


def _state(tmp_path):
    return ResearchState(run=ResearchRun(
        run_id="contract-run", workspace_dir=str(tmp_path), research_goal="goal",
    ))


def test_run_task_inherits_repro_context_and_routes_to_reproagent(tmp_path):
    state = _state(tmp_path)
    state.tasks.append(AgentTask(
        id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        status=TaskStatus.completed,
        input={"paper_url": "paper", "repo_url": "repo"},
    ))
    action = {
        "type": "run_task",
        "plan": {"kind": "run_task", "command_goal": "run 3 epochs"},
    }

    executor, kind, capability, plan = normalize_recommended_action(action, state)

    assert executor == Producer.ReproAgent
    assert kind == AgentKind.repro_task
    assert capability == "run_experiment"
    assert plan["repo_url"] == "repo"
    assert plan["experiment_goal"] == "run 3 epochs"


def test_ambiguous_run_task_fails_closed(tmp_path):
    state = _state(tmp_path)
    action = {"type": "run_task", "plan": {"kind": "run_task",
                                              "command_goal": "run it"}}

    try:
        normalize_recommended_action(action, state)
    except ValueError as exc:
        assert "no repository" in str(exc)
    else:
        raise AssertionError("ambiguous run_task must not be guessed")


def test_task_fingerprint_ignores_description_but_not_goal():
    first = task_fingerprint(
        Producer.ReproAgent, "run_experiment",
        {"description": "a", "repo_url": "r", "experiment_goal": "2 epochs"},
    )
    same = task_fingerprint(
        Producer.ReproAgent, "run_experiment",
        {"description": "b", "repo_url": "r", "experiment_goal": "2 epochs"},
    )
    different = task_fingerprint(
        Producer.ReproAgent, "run_experiment",
        {"repo_url": "r", "experiment_goal": "3 epochs"},
    )
    assert first == same
    assert first != different


def test_finish_rejects_required_pending_task(tmp_path):
    state = _state(tmp_path)
    state.artifacts.append(Artifact(
        id="a", type=ArtifactType.report, producer=Producer.ResAgent, path="a.md",
    ))
    state.tasks.append(AgentTask(
        id="task_001", agent=Producer.CodingAgent,
        kind=AgentKind.coding_task, required=True,
    ))

    check = validate_finish(state)

    assert not check.allowed
    assert check.task_ids == ("task_001",)
    assert {"action": "finish"} not in allowed_action_candidates(state)


def test_finish_rejects_immediately_after_error(tmp_path):
    state = _state(tmp_path)
    state.artifacts.append(Artifact(
        id="a", type=ArtifactType.report, producer=Producer.ResAgent, path="a.md",
    ))
    state.observations.append(Observation(
        action=ActionName.call_coding_agent, result="error", detail="wrong owner",
    ))

    check = validate_finish(state)

    assert not check.allowed
    assert "error" in check.reason


def test_terminal_run_has_no_action_candidates(tmp_path):
    state = _state(tmp_path)
    state.run.status = RunStatus.completed
    assert allowed_action_candidates(state) == []


def test_resagent_ask_user_task_is_an_allowed_candidate(tmp_path):
    state = _state(tmp_path)
    state.tasks.append(AgentTask(
        id="task_001", agent=Producer.ResAgent, kind=AgentKind.ask_user,
        capability="request_user_input", input={"question": "Proceed?"},
    ))
    assert {"action": "ask_user", "task_id": "task_001"} in allowed_action_candidates(state)


def test_priority_does_not_make_research_work_optional():
    from resagent.models import TaskPriority

    assert required_from_priority(TaskPriority.medium, {}) is True
    assert required_from_priority(TaskPriority.low, {}) is True
    assert required_from_priority(
        TaskPriority.high, {"required": False},
    ) is False
