#!/usr/bin/env python3
"""Fast deterministic system acceptance test.

Exercises ResAgent plus all three adapter boundaries without network, GPU, or
LLM calls. Intended for every local change and CI run.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

import yaml

from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter
from resagent.controller import Controller
from resagent.models import (
    ActionName, AgentKind, AgentTask, Producer, RunStatus, TaskStatus,
)
from resagent.planner import PlannedAction
from resagent.state import init_state, load_state, save_state, submit_user_response
from resagent.task_contracts import validate_finish
from resagent.workspace_layout import WorkspaceLayout


class ScriptedPlanner:
    def __init__(self, actions):
        self.actions = list(actions)

    def choose_action(self, state):
        if not self.actions:
            raise AssertionError("scripted planner exhausted")
        return self.actions.pop(0)

    def classify_failure(self, task_id, error):
        return {"category": "unknown"}


def assert_registered_artifacts_exist(state, layout: WorkspaceLayout) -> None:
    missing = [
        artifact.path for artifact in state.artifacts
        if not (layout.run_dir / artifact.path).exists()
    ]
    assert not missing, f"registered artifacts do not exist: {missing}"


def run_acceptance(workspace: Path) -> dict:
    started = time.monotonic()
    run_id = "deterministic-four-module"
    state = init_state(run_id, str(workspace), "Exercise all module contracts")
    layout = WorkspaceLayout(str(workspace), run_id)
    assertions: list[str] = []

    # ExpAgent boundary: create an initial reproduction plan.
    exp = ExpAgentAdapter(mock=True)
    initial = exp.advise(state, layout)
    state.artifacts.append(initial["artifact"])
    repro = next(task for task in initial["tasks"]
                 if task.agent == Producer.ReproAgent)
    repro.required = True
    state.tasks.append(repro)
    assertions.append("ExpAgent produced a typed ReproAgent task")

    # ReproAgent boundary: execute the first bounded experiment.
    repro_adapter = ReproAgentAdapter(mock=True)
    first_result = repro_adapter.execute(repro, layout)
    state.artifacts.append(first_result["artifact"])
    repro.artifacts.append(first_result["artifact"].id)
    repro.status = TaskStatus.completed
    assertions.append("ReproAgent produced a registered result artifact")

    # ExpAgent follow-up: an execute_experiment action becomes a second
    # ReproAgent task instead of an ExpAgent advisory task.
    exp._state = state
    followups = exp._actions_to_tasks([{
        "action_id": "verify_again",
        "capability": "execute_experiment",
        "objective": "run one epoch again",
        "rationale": "Verify the baseline after a code change",
        "depends_on": [],
        "project_ref": "project",
        "required": True,
        "expected_metrics": [],
        "requires_gpu": False,
    }], source="followup-decision", next_num=state.next_task_number())
    assert len(followups) == 1
    followup = followups[0]
    assert followup.agent == Producer.ReproAgent
    state.tasks.append(followup)
    assertions.append("Follow-up execute_experiment routed to ReproAgent")

    # CodingAgent boundary and parent session linkage.
    coding = AgentTask(
        id=f"task_{state.next_task_number():03d}",
        agent=Producer.CodingAgent,
        kind=AgentKind.coding_task,
        capability="modify_code",
        required=True,
        input={"workspace_path": str(workspace / "fixture-repo"),
               "task_goal": "Add metric logging"},
    )
    state.tasks.append(coding)
    code_result = CodingAgentAdapter(mock=True).execute(coding, layout)
    state.artifacts.append(code_result["artifact"])
    coding.artifacts.append(code_result["artifact"].id)
    coding.status = TaskStatus.completed
    card_path = layout.codingagent_attempt_dir(
        int(coding.id.split("_")[1]), 1,
    ) / "session.yaml"
    card = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    assert card["parent"]["run_id"] == run_id
    assert card["parent"]["task_id"] == coding.id
    assertions.append("CodingAgent session links to the parent run/task")

    # Run the second reproduction and verify both use the same run namespace.
    second_result = repro_adapter.execute(followup, layout)
    state.artifacts.append(second_result["artifact"])
    followup.artifacts.append(second_result["artifact"].id)
    followup.status = TaskStatus.completed
    assert repro.id != followup.id
    assertions.append("Two distinct ReproTasks completed in one run")

    # ask_user persistence and resume.
    question_task = AgentTask(
        id=f"task_{state.next_task_number():03d}",
        agent=Producer.ResAgent,
        kind=AgentKind.ask_user,
        capability="ask_user",
        required=True,
        input={"question": "Accept the bounded result?"},
    )
    state.tasks.append(question_task)
    planner = ScriptedPlanner([
        PlannedAction(ActionName.ask_user, {"task_id": question_task.id}),
    ])
    ctrl = Controller(
        planner, exp, CodingAgentAdapter(mock=True), repro_adapter,
    )
    paused = ctrl.step(state)
    assert paused.result == "user_response_required"
    save_state(state)
    restored = load_state(str(workspace), run_id)
    assert restored is not None and restored.pending_question is not None
    submit_user_response(
        restored, restored.pending_question.question_id, "accepted",
    )
    assert restored.find_task(question_task.id).status == TaskStatus.completed
    assertions.append("ask_user survived save/load and resumed exactly once")

    # Finish gate and terminal guard.
    # This is an engineering smoke test of module contracts, not a scientific
    # run, so completed experiments need no scientific analysis here.
    restored.analysis_required = False
    check = validate_finish(restored)
    assert check.allowed, check
    finish_ctrl = Controller(
        ScriptedPlanner([PlannedAction(
            ActionName.finish, {"summary": "acceptance complete"},
        )]),
        exp, CodingAgentAdapter(mock=True), repro_adapter,
    )
    finished = finish_ctrl.step(restored)
    assert finished.result == "ok"
    assert restored.run.status == RunStatus.completed
    terminal = finish_ctrl.step(restored)
    assert terminal.result == "terminal"
    assertions.append("finish gate passed and completed run stayed terminal")

    assert_registered_artifacts_exist(restored, layout)
    assertions.append("Every registered artifact path exists")
    for module in ("expagent", "codingagent", "reproagent"):
        cards = list(layout.run_dir.rglob("session.yaml"))
        assert any(module in path.as_posix() for path in cards)
    save_state(restored)

    return {
        "status": "passed",
        "run_id": run_id,
        "workspace": str(workspace / run_id),
        "duration_seconds": round(time.monotonic() - started, 3),
        "assertions": assertions,
        "tasks": len(restored.tasks),
        "artifacts": len(restored.artifacts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="Persistent output directory")
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()

    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        report = run_acceptance(workspace)
    else:
        with tempfile.TemporaryDirectory(prefix="resagent-acceptance-") as tmp:
            report = run_acceptance(Path(tmp))

    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
