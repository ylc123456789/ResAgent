"""Regression tests for defects found in the 2026-08-22 review.

Locks in behavior for the fixes made on the readability-cleanup branch:
- `resagent step` actually advances the run (was a silent no-op)
- planner fails closed on an invalid LLM action (was silently finishing)
- report generation writes execution_plan.md / summary.md / artifacts/index.json
"""

import sys
from contextlib import redirect_stdout
from io import StringIO

import pytest

import resagent.main as cli
from resagent.config import load_config
from resagent.models import Artifact, ArtifactType, Producer, ResearchRun, ResearchState
from resagent.orchestrator import init_run
from resagent.persistence.report import generate_all
from resagent.persistence.state import load_state
from resagent.controller.planner import Planner, PlannerError


def _write_module_card(tmp_path, name, capabilities, side_effects):
    d = tmp_path / name
    d.mkdir()
    (d / "agent.yaml").write_text(
        f"name: {name}\nrole: test\ncapabilities: [{capabilities}]\n"
        f"side_effects: {side_effects}\nstatus: available\n",
        encoding="utf-8",
    )
    return str(d)


def _write_config(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "agents:\n"
        f"  expagent_path: {_write_module_card(tmp_path, 'expagent', 'analyze_results, search_literature', 'none')}\n"
        f"  codingagent_path: {_write_module_card(tmp_path, 'codingagent', 'modify_code', 'workspace')}\n"
        f"  reproagent_path: {_write_module_card(tmp_path, 'reproagent', 'reproduce_experiment, execute_experiment', 'workspace_and_environment')}\n",
        encoding="utf-8",
    )
    return cfg_path


def test_step_subcommand_advances_run(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path)
    cfg = load_config(str(cfg_path))
    state = init_run(goal="Goal", workspace_root=str(tmp_path), config=cfg)
    run_id = state.run.run_id
    before = len(state.observations)

    monkeypatch.setattr(sys, "argv", [
        "resagent", "step", "--workspace", str(tmp_path),
        "--run-id", run_id, "--mock", "--config", str(cfg_path),
    ])
    out = StringIO()
    with redirect_stdout(out):
        cli.main()

    after = load_state(str(tmp_path), run_id)
    assert len(after.observations) > before, "step must advance the run by one step"
    assert after.run.status.value == "running", (
        "a non-terminal single step must keep the run running, not interrupted"
    )
    assert "Run status:" in out.getvalue()


def test_generate_all_writes_report_files(tmp_path):
    state = ResearchState(run=ResearchRun(
        run_id="r1", workspace_dir=str(tmp_path), research_goal="Goal",
    ))
    state.artifacts.append(Artifact(
        id="a1", type=ArtifactType.report, producer=Producer.ResAgent,
        path="p", summary="s",
    ))
    generate_all(state)
    run_dir = tmp_path / "r1"
    assert (run_dir / "execution_plan.md").exists()
    assert (run_dir / "summary.md").exists()
    assert (run_dir / "artifacts" / "index.json").exists()


def test_parse_response_fails_closed_on_invalid_action():
    planner = Planner(mock=True)
    with pytest.raises(PlannerError):
        planner._parse_response('{"action": "not_a_real_action"}')


def test_env_id_path_traversal_is_rejected(tmp_path):
    import json
    from resagent.resources import _validate_env_id, acquire_lease, read_manifest

    for bad in ["", ".", "..", "../evil", "a/b", "a\\b", "/abs"]:
        with pytest.raises(ValueError):
            _validate_env_id(bad)

    root = str(tmp_path)
    for bad in ["../evil", "a/b", "/abs"]:
        with pytest.raises(ValueError):
            read_manifest(root, bad)
        with pytest.raises(ValueError):
            acquire_lease(root, bad, "run", "task")

    # legal layout unchanged: manifest readable from root/environments/<id>
    env_dir = tmp_path / "environments" / "env1"
    env_dir.mkdir(parents=True)
    (env_dir / "manifest.json").write_text(
        json.dumps({"env_id": "env1", "state": "ready"}), encoding="utf-8",
    )
    manifest = read_manifest(root, "env1")
    assert manifest is not None
    assert manifest["env_id"] == "env1"
    assert manifest["state"] == "ready"


def test_explicit_finish_does_not_consume_api_budget(tmp_path):
    from resagent.models import Artifact, ArtifactType, Producer, RunStatus
    from resagent.persistence.state import append_user_directive, init_state
    from resagent.controller import Controller
    from resagent.adapters.expagent import ExpAgentAdapter
    from resagent.adapters.codingagent import CodingAgentAdapter
    from resagent.adapters.reproagent import ReproAgentAdapter
    from tests.v2_registry import make_registry

    class PanicPlanner:
        def choose_action(self, state):
            raise AssertionError("planner must not run for explicit finish")

    state = init_state("finish-budget", str(tmp_path), "Goal")
    state.artifacts.append(Artifact(
        id="result", type=ArtifactType.report, producer=Producer.ResAgent,
        path="result.md",
    ))
    append_user_directive(state, "收口")
    ctrl = Controller(
        PanicPlanner(), ExpAgentAdapter(mock=True, registry=make_registry()),
        CodingAgentAdapter(mock=True), ReproAgentAdapter(mock=True),
    )

    ctrl.step(state)

    assert state.run.status == RunStatus.completed
    assert state.budget.api_calls_used == 0
