"""Phase 0 compatibility locks for behavior-preserving refactors."""

from __future__ import annotations

import hashlib
import sys
from contextlib import redirect_stdout
from io import StringIO

import resagent
import resagent.main as cli
from resagent.models import AgentTask, Artifact, Observation, ResearchRun, ResearchState
from resagent.controller.prompts import CHAT_SYSTEM, CONTROLLER_SYSTEM, FAILURE_CLASSIFIER, SUMMARY_PROMPT
from resagent.persistence.workspace import WorkspaceLayout


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_public_package_contract() -> None:
    assert resagent.__version__ == "0.1.0"


def test_cli_help_contract(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["resagent", "--help"])
    output = StringIO()
    with redirect_stdout(output):
        try:
            cli.main()
        except SystemExit as exc:
            assert exc.code == 0
    assert _sha256(output.getvalue()) == "b2e1c55a213995292f55f488a7f75b9dc04ec045be9fee93a9f409973dda9423"


def test_prompt_contracts() -> None:
    # CONTROLLER_SYSTEM is now a dynamic template: no hard-coded team table,
    # with a {capabilities} placeholder rendered from the capability registry.
    assert "{capabilities}" in CONTROLLER_SYSTEM
    assert "Your team" not in CONTROLLER_SYSTEM
    assert [_sha256(prompt) for prompt in (
        CHAT_SYSTEM,
        FAILURE_CLASSIFIER,
        SUMMARY_PROMPT,
    )] == [
        "431c36ee84bbf6c395036be322e912bab670a7615f8fc3ec506c1bb77f0fab75",
        "2a1a5e2b70975c28a778d2b17a7f092f028c4f133a39590bfd905dbc991f8936",
        "0ef333277a243b417592aa4a92978ce8f2811854cb0a40477bdf4ab615e2c2e5",
    ]


def test_persisted_model_field_contracts() -> None:
    assert list(ResearchState.model_fields) == [
        "run", "analysis_required", "current_summary", "artifacts", "resources",
        "tasks", "decisions", "observations", "budget", "user_directives",
        "pending_question", "answered_questions",
    ]
    assert list(AgentTask.model_fields) == [
        "id", "source", "agent", "kind", "status", "priority", "capability",
        "required", "analysis_required", "fingerprint", "action_id", "depends_on", "project_ref",
        "input", "artifacts", "attempts", "error", "warnings",
    ]
    assert list(Artifact.model_fields) == [
        "id", "type", "producer", "path", "summary", "created_at", "metadata",
    ]
    assert list(Observation.model_fields) == [
        "timestamp", "action", "result", "detail", "artifact_ids", "task_ids",
    ]

    state = ResearchState(run=ResearchRun(
        run_id="res-phase0", workspace_dir="/tmp/runs", research_goal="freeze behavior"
    ))
    restored = ResearchState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_workspace_layout_contract(tmp_path) -> None:
    layout = WorkspaceLayout(str(tmp_path), "res-phase0")
    assert layout.state_json == tmp_path / "res-phase0" / "state.json"
    assert layout.expagent_run_dir(2) == (
        tmp_path / "res-phase0" / "tasks" / "expagent" / "decision_002" / "run"
    )
    assert layout.codingagent_attempt_dir(3, 2) == (
        tmp_path / "res-phase0" / "tasks" / "codingagent" / "task_003" / "attempt_002"
    )
    assert layout.reproagent_workspace(4, 3) == (
        tmp_path / "res-phase0" / "tasks" / "reproagent" / "task_004"
        / "attempt_003" / "repo_workspace"
    )
