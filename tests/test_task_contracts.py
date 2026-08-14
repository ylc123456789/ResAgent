"""Contract tests for V2 capability routing and run lifecycle invariants."""

import textwrap

import pytest

from resagent.capabilities import CapabilityError, CapabilityRegistry
from resagent.config import Config
from resagent.models import (
    ActionName, AgentKind, AgentTask, Artifact, ArtifactType, Observation,
    Producer, ResearchRun, ResearchState, RunStatus, TaskStatus,
)
from resagent.task_contracts import (
    allowed_action_candidates, analysis_coverage, ensure_analysis_coverage,
    resolve_action, task_fingerprint, validate_finish,
)


def _state(tmp_path, analysis_required: bool = True) -> ResearchState:
    state = ResearchState(run=ResearchRun(
        run_id="contract-run", workspace_dir=str(tmp_path), research_goal="goal",
    ))
    state.analysis_required = analysis_required
    return state


def _experiment(task_id="task_001", status=TaskStatus.completed) -> AgentTask:
    return AgentTask(
        id=task_id, agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        capability="execute_experiment", required=True, status=status,
        artifacts=["a1"],
    )


# ── capability routing ──────────────────────────────────────────────────────

def test_resolve_action_routes_all_six_capabilities():
    cases = {
        "modify_code": (Producer.CodingAgent, AgentKind.coding_task, "modify_code"),
        "reproduce_experiment": (Producer.ReproAgent, AgentKind.repro_task, "reproduce_experiment"),
        "execute_experiment": (Producer.ReproAgent, AgentKind.repro_task, "execute_experiment"),
        "analyze_results": (Producer.ExpAgent, AgentKind.advise, "analyze_results"),
        "search_literature": (Producer.ExpAgent, AgentKind.advise, "search_literature"),
        "ask_user": (Producer.ResAgent, AgentKind.ask_user, "ask_user"),
    }
    for capability, expected in cases.items():
        assert resolve_action({"capability": capability}) == expected


def test_resolve_action_unknown_capability_fails_closed():
    with pytest.raises(CapabilityError):
        resolve_action({"capability": "run_task"})
    with pytest.raises(CapabilityError):
        resolve_action({})


def test_registry_and_frozen_vocabulary_agree():
    """Chat router and controller resolve the same capability to one module."""
    registry = _registry()
    for capability in ("modify_code", "reproduce_experiment", "execute_experiment",
                       "analyze_results", "search_literature"):
        via_registry = registry.resolve(capability)
        via_frozen = resolve_action({"capability": capability}, registry)[0]
        assert via_registry == via_frozen


def _registry() -> CapabilityRegistry:
    import tempfile
    from pathlib import Path
    base = Path(tempfile.mkdtemp())
    cards = {
        "expagent": "capabilities: [analyze_results, search_literature]\nside_effects: none\n",
        "codingagent": "capabilities: [modify_code]\nside_effects: workspace\n",
        "reproagent": "capabilities: [reproduce_experiment, execute_experiment]\nside_effects: workspace_and_environment\n",
    }
    cfg = Config()
    for name, body in cards.items():
        d = base / name
        d.mkdir()
        (d / "agent.yaml").write_text(
            f"name: {name}\n" + body, encoding="utf-8",
        )
    cfg.agents.expagent = str(base / "expagent")
    cfg.agents.codingagent = str(base / "codingagent")
    cfg.agents.reproagent = str(base / "reproagent")
    reg = CapabilityRegistry(cfg)
    reg.load()
    return reg


# ── fingerprint ─────────────────────────────────────────────────────────────

def test_task_fingerprint_ignores_description_but_not_goal():
    first = task_fingerprint(
        Producer.ReproAgent, "execute_experiment",
        {"description": "a", "experiment_goal": "2 epochs"},
    )
    same = task_fingerprint(
        Producer.ReproAgent, "execute_experiment",
        {"description": "b", "experiment_goal": "2 epochs"},
    )
    different = task_fingerprint(
        Producer.ReproAgent, "execute_experiment",
        {"experiment_goal": "3 epochs"},
    )
    assert first == same
    assert first != different


# ── finish gate ─────────────────────────────────────────────────────────────

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
        capability="ask_user", input={"question": "Proceed?"},
    ))
    assert {"action": "ask_user", "task_id": "task_001"} in allowed_action_candidates(state)


# ── scientific closure ──────────────────────────────────────────────────────

def test_analysis_coverage_missing_then_covered(tmp_path):
    state = _state(tmp_path)
    state.tasks.append(_experiment())
    state.artifacts.append(Artifact(
        id="a1", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="result.md",
    ))
    assert analysis_coverage(state, "task_001") == "missing"

    state.tasks.append(AgentTask(
        id="task_002", agent=Producer.ExpAgent, kind=AgentKind.advise,
        capability="analyze_results", status=TaskStatus.completed,
        depends_on=["task_001"], artifacts=["a2"],
    ))
    state.artifacts.append(Artifact(
        id="a2", type=ArtifactType.scientific_decision, producer=Producer.ExpAgent,
        path="decision.json",
    ))
    assert analysis_coverage(state, "task_001") == "covered"


def test_analysis_coverage_not_required_for_smoke(tmp_path):
    state = _state(tmp_path, analysis_required=False)
    state.tasks.append(_experiment())
    state.artifacts.append(Artifact(
        id="a1", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="result.md",
    ))
    assert analysis_coverage(state, "task_001") == "not_required"


def test_ensure_analysis_coverage_creates_exactly_one_task(tmp_path):
    state = _state(tmp_path)
    experiment = _experiment()
    state.tasks.append(experiment)
    state.artifacts.append(Artifact(
        id="a1", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="result.md",
    ))

    created = ensure_analysis_coverage(state, experiment)
    assert created is not None
    assert created.agent == Producer.ExpAgent
    assert created.capability == "analyze_results"
    assert created.depends_on == ["task_001"]
    assert created.required is True

    # Second call must not duplicate.
    assert ensure_analysis_coverage(state, experiment) is None
    assert sum(1 for t in state.tasks if t.capability == "analyze_results") == 1


def test_unanalyzed_experiment_blocks_finish(tmp_path):
    state = _state(tmp_path)
    state.tasks.append(_experiment())
    state.artifacts.append(Artifact(
        id="a1", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="result.md",
    ))
    check = validate_finish(state)
    assert not check.allowed
    assert "analyzed" in check.reason
    assert check.task_ids == ("task_001",)


def test_smoke_test_does_not_block_finish(tmp_path):
    state = _state(tmp_path, analysis_required=False)
    state.tasks.append(_experiment())
    state.artifacts.append(Artifact(
        id="a1", type=ArtifactType.repro_result, producer=Producer.ReproAgent,
        path="result.md",
    ))
    assert validate_finish(state).allowed
