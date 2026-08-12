#!/usr/bin/env python3
"""Real LLM/GPU acceptance suite for ResAgent and its submodules."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml

from resagent.config import load_config
from resagent.models import (
    ActionName, AgentKind, AgentTask, Producer, RunStatus, TaskStatus,
)
from resagent.orchestrator import build_controller, init_run
from resagent.planner import PlannedAction
from resagent.state import save_state


class SequencePlanner:
    """Deterministic top-level dispatcher; submodules remain real."""

    def __init__(self, actions):
        self.actions = list(actions)

    def choose_action(self, state):
        if not self.actions:
            raise AssertionError("acceptance dispatcher exhausted")
        return self.actions.pop(0)

    def classify_failure(self, task_id, error):
        return {"category": "unknown", "recommended_action": "investigate"}


def _step_until_stop(state, controller, max_steps=12):
    observations = []
    for _ in range(max_steps):
        obs = controller.step(state)
        save_state(state)
        observations.append(obs)
        print(f"[{state.run.run_id}] {obs.action.value}: {obs.result}")
        if obs.result in {"error", "rejected"}:
            raise AssertionError(f"{obs.action.value} failed: {obs.detail}")
        if obs.action == ActionName.finish or obs.result == "user_response_required":
            break
    return observations


def _assert_artifacts_exist(state):
    root = Path(state.run.workspace_dir) / state.run.run_id
    missing = [artifact.path for artifact in state.artifacts
               if not (root / artifact.path).exists()]
    assert not missing, f"missing registered artifacts: {missing}"


def _session_cards(state):
    root = Path(state.run.workspace_dir) / state.run.run_id
    return list(root.rglob("session.yaml"))


def _assert_parent_links(state, modules):
    found = set()
    for path in _session_cards(state):
        card = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        module = str(card.get("module", "")).lower()
        if module not in modules:
            continue
        parent = card.get("parent") or {}
        assert parent.get("run_id") == state.run.run_id, (
            f"bad parent in {path}: {parent}"
        )
        found.add(module)
    assert modules <= found, f"missing session cards for: {modules - found}"


def _artifact_text(state) -> str:
    root = Path(state.run.workspace_dir) / state.run.run_id
    chunks = []
    for artifact in state.artifacts:
        path = root / artifact.path
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _setup_fixture(workspace: Path) -> Path:
    repo = workspace / "fixtures" / f"coding-{int(time.time())}"
    repo.mkdir(parents=True, exist_ok=False)
    (repo / "train.py").write_text(
        "import json\n"
        "def train():\n"
        "    accuracy = 0.75\n"
        "    json.dump({'accuracy': accuracy}, open('result.json', 'w'))\n"
        "if __name__ == '__main__': train()\n",
        encoding="utf-8",
    )
    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "acceptance@example.com"],
        ["git", "config", "user.name", "Acceptance Test"],
        ["git", "add", "train.py"],
        ["git", "commit", "-m", "fixture"],
    ]
    for command in commands:
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    return repo


def case_coding(config, workspace: Path) -> dict:
    repo = _setup_fixture(workspace)
    state = init_run(
        "Add a loss field equal to 1.0 - accuracy to the result JSON in "
        f"{repo}/train.py. Do not change accuracy. Run the script and verify both fields.",
        workspace_root=str(workspace / "runs"), config=config,
    )
    controller = build_controller(config, mock=False)
    observations = _step_until_stop(state, controller, max_steps=10)
    completed = [task for task in state.tasks
                 if task.agent == Producer.CodingAgent
                 and task.status == TaskStatus.completed]
    assert completed, "no CodingAgent task completed"
    assert state.run.status == RunStatus.completed, f"run ended as {state.run.status}"
    result = json.loads((repo / "result.json").read_text(encoding="utf-8"))
    assert "loss" in result and result["accuracy"] == 0.75
    _assert_artifacts_exist(state)
    _assert_parent_links(state, {"expagent", "codingagent"})
    return {"run_id": state.run.run_id, "steps": len(observations),
            "completed_coding_tasks": len(completed)}


def case_repro(config, workspace: Path) -> dict:
    state = init_run(
        "Reproduce the Neural ODE MNIST experiment from "
        "https://arxiv.org/abs/1806.07366 using "
        "https://github.com/rtqichen/torchdiffeq.git. Run exactly 3 epochs "
        "on GPU and report final/best accuracy, runtime, and deviations. "
        "This is a bounded integration test, not a full paper reproduction.",
        workspace_root=str(workspace / "runs"), config=config,
    )
    controller = build_controller(config, mock=False)
    observations = _step_until_stop(state, controller, max_steps=12)
    completed = [task for task in state.tasks
                 if task.agent == Producer.ReproAgent
                 and task.status == TaskStatus.completed]
    assert completed, "no ReproAgent task completed"
    assert state.run.status == RunStatus.completed, f"run ended as {state.run.status}"
    evidence = _artifact_text(state).lower()
    assert any(token in evidence for token in ("gpu", "cuda", "4090")), (
        "no GPU evidence in registered result artifacts")
    _assert_artifacts_exist(state)
    _assert_parent_links(state, {"expagent", "reproagent"})
    return {"run_id": state.run.run_id, "steps": len(observations),
            "completed_repro_tasks": len(completed)}


def case_env_reuse(config, workspace: Path) -> dict:
    state = init_run(
        "Run two bounded MNIST experiments in one project environment.",
        workspace_root=str(workspace / "runs"), config=config,
    )
    for index, epochs in enumerate((1, 2), start=1):
        state.tasks.append(AgentTask(
            id=f"task_{index:03d}", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task, capability="run_experiment",
            required=True,
            input={
                "repo_url": "https://github.com/pytorch/examples.git",
                "experiment_goal": (
                    "Run mnist/main.py on GPU with "
                    f"--epochs={epochs}; report test accuracy, loss and runtime."
                ),
            },
        ))
    controller = build_controller(config, mock=False)
    controller.planner = SequencePlanner([
        PlannedAction(ActionName.call_repro_agent, {"task_id": "task_001"}),
        PlannedAction(ActionName.call_repro_agent, {"task_id": "task_002"}),
        PlannedAction(ActionName.finish, {"summary": "two runs completed"}),
    ])
    observations = _step_until_stop(state, controller, max_steps=3)
    completed = [task for task in state.tasks
                 if task.agent == Producer.ReproAgent
                 and task.status == TaskStatus.completed]
    assert len(completed) == 2, f"expected 2 ReproTasks, got {len(completed)}"
    envs = set()
    for path in _session_cards(state):
        card = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if str(card.get("module", "")).lower() == "reproagent":
            env = (card.get("bindings") or {}).get("conda_env")
            if env:
                envs.add(str(env))
    assert len(envs) == 1, f"expected one shared environment, got {envs}"
    assert state.run.status == RunStatus.completed
    _assert_artifacts_exist(state)
    _assert_parent_links(state, {"reproagent"})
    return {"run_id": state.run.run_id, "steps": len(observations),
            "completed_repro_tasks": 2, "environments": sorted(envs)}


CASES = {"coding": case_coding, "repro": case_repro,
         "env-reuse": case_env_reuse}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="", help="Optional config YAML; defaults are used when omitted",
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    args = parser.parse_args()
    config = load_config(args.config)
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    selected = list(CASES) if args.case == "all" else [args.case]
    report = {"status": "passed", "cases": {}, "started_at": time.time()}
    try:
        for name in selected:
            started = time.monotonic()
            result = CASES[name](config, workspace)
            result["duration_seconds"] = round(time.monotonic() - started, 2)
            report["cases"][name] = result
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report_path = workspace / "logs" / "cloud_acceptance_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
