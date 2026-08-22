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
