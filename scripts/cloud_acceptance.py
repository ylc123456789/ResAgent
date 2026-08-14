#!/usr/bin/env python3
"""Real LLM/GPU acceptance suite for ResAgent and its submodules."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from resagent.config import load_config
from resagent.models import (
    ActionName, AgentKind, AgentTask, Artifact, ArtifactType, Producer,
    RunStatus, TaskStatus,
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
    try:
        for _ in range(max_steps):
            obs = controller.step(state)
            save_state(state)
            observations.append(obs)
            print(f"[{state.run.run_id}] {obs.action.value}: {obs.result}")
            if obs.result in {"error", "rejected"}:
                raise AssertionError(f"{obs.action.value} failed: {obs.detail}")
            if obs.action == ActionName.finish or obs.result == "user_response_required":
                break
        else:
            raise AssertionError(f"controller did not stop within {max_steps} steps")
    except KeyboardInterrupt:
        state.run.status = RunStatus.interrupted
        save_state(state)
        raise
    except Exception:
        if state.run.status not in {RunStatus.paused, RunStatus.completed}:
            state.run.status = RunStatus.failed
        save_state(state)
        raise
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


def case_repro(
    config, workspace: Path, local_repo: Path | None = None,
) -> dict:
    if local_repo is not None:
        source = workspace / "fixtures" / f"torchdiffeq-local-{int(time.time())}"
        shutil.copytree(
            local_repo, source, symlinks=True,
            ignore=shutil.ignore_patterns(
                "experiment*", "__pycache__", "*.pyc",
            ),
        )
        repository_clause = f"the local repository {source}"
    else:
        source = None
        repository_clause = "https://github.com/rtqichen/torchdiffeq.git"
    state = init_run(
        "Using " + repository_clause +
        ", reproduce the Neural ODE MNIST experiment from "
        "https://arxiv.org/abs/1806.07366"
        ". Run exactly 3 epochs "
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
    unresolved = [
        task.id for task in state.tasks
        if task.required and task.status != TaskStatus.completed
    ]
    assert not unresolved, f"required tasks unresolved: {unresolved}"
    assert state.run.status == RunStatus.completed, f"run ended as {state.run.status}"
    evidence = _artifact_text(state).lower()
    assert any(token in evidence for token in ("gpu", "cuda", "4090")), (
        "no GPU evidence in registered result artifacts")
    _assert_artifacts_exist(state)
    _assert_parent_links(state, {"expagent", "reproagent"})
    return {"run_id": state.run.run_id, "steps": len(observations),
            "completed_repro_tasks": len(completed),
            "local_source": str(source) if source else ""}


def case_env_reuse(config, workspace: Path) -> dict:
    state = init_run(
        "Run two bounded MNIST experiments in one project environment.",
        workspace_root=str(workspace / "runs"), config=config,
    )
    for index, epochs in enumerate((1, 2), start=1):
        state.tasks.append(AgentTask(
            id=f"task_{index:03d}", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task, capability="execute_experiment",
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


def case_dependency_chain(config, workspace: Path) -> dict:
    repo = workspace / "fixtures" / f"dependency-{int(time.time())}"
    repo.mkdir(parents=True, exist_ok=False)
    (repo / "README.md").write_text(
        "# Dependency fixture\n\nRun `python train.py`; it prints VALUE.\n",
        encoding="utf-8",
    )
    (repo / "train.py").write_text(
        "EXPECTED_VALUE = 1\nprint(f'VALUE={EXPECTED_VALUE}')\n",
        encoding="utf-8",
    )
    for command in (["git", "init"],
                    ["git", "config", "user.email", "acceptance@example.com"],
                    ["git", "config", "user.name", "Acceptance Test"],
                    ["git", "add", "."],
                    ["git", "commit", "-m", "fixture"]):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)

    state = init_run(
        "Modify a project and verify the modified code in an isolated experiment.",
        workspace_root=str(workspace / "runs"), config=config,
    )
    coding = AgentTask(
        id="task_001", agent=Producer.CodingAgent,
        kind=AgentKind.coding_task, capability="modify_code", required=True,
        action_id="patch_value", project_ref="fixture",
        input={
            "workspace_path": str(repo),
            "task_goal": "Change EXPECTED_VALUE in train.py from 1 to 2.",
            "constraints": ["Only change the EXPECTED_VALUE assignment."],
            "verify_commands": ["python train.py"],
        },
    )
    repro = AgentTask(
        id="task_002", agent=Producer.ReproAgent,
        kind=AgentKind.repro_task, capability="execute_experiment", required=True,
        action_id="verify_value", depends_on=[coding.id], project_ref="fixture",
        input={
            "workspace_intent": "isolated",
            "repo_url": str(repo),
            "source_workspace": str(repo),
            "experiment_goal": (
                "Run python train.py once and report the exact VALUE output. "
                "The required acceptance value is VALUE=2."
            ),
        },
    )
    state.tasks.extend([coding, repro])
    controller = build_controller(config, mock=False)
    controller.planner = SequencePlanner([
        PlannedAction(ActionName.call_coding_agent, {"task_id": coding.id}),
        PlannedAction(ActionName.call_repro_agent, {"task_id": repro.id}),
        PlannedAction(ActionName.finish, {"summary": "dependency chain completed"}),
    ])
    observations = _step_until_stop(state, controller, max_steps=3)
    assert coding.status == TaskStatus.completed
    assert repro.status == TaskStatus.completed
    assert "EXPECTED_VALUE = 2" in (repo / "train.py").read_text(encoding="utf-8")
    copied = (Path(state.run.workspace_dir) / state.run.run_id / "tasks" /
              "reproagent" / "task_002" / "attempt_001" /
              "repo_workspace" / "repo" / "train.py")
    assert "EXPECTED_VALUE = 2" in copied.read_text(encoding="utf-8")
    assert "value=2" in _artifact_text(state).lower()
    assert state.run.status == RunStatus.completed
    _assert_artifacts_exist(state)
    _assert_parent_links(state, {"codingagent", "reproagent"})
    return {"run_id": state.run.run_id, "steps": len(observations),
            "source_workspace": str(repo), "snapshot": str(copied)}


def case_fan_in_analysis(config, workspace: Path) -> dict:
    """Verify real ExpAgent receives all prerequisite result artifacts."""
    state = init_run(
        "Compare two completed bounded experiments and explain which result is better.",
        workspace_root=str(workspace / "runs"), config=config,
    )
    run_root = Path(state.run.workspace_dir) / state.run.run_id
    dependencies = []
    for number, accuracy in ((1, 0.91), (2, 0.94)):
        result_path = run_root / "fixtures" / f"result_{number}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps({"accuracy": accuracy, "runtime_seconds": 10 + number}),
            encoding="utf-8",
        )
        artifact_id = f"fixture_result_{number}"
        state.artifacts.append(Artifact(
            id=artifact_id, type=ArtifactType.repro_result,
            producer=Producer.ReproAgent,
            path=str(result_path.relative_to(run_root)),
            summary=f"bounded experiment {number}: accuracy={accuracy}",
        ))
        dependency = AgentTask(
            id=f"task_{number:03d}", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task, status=TaskStatus.completed,
            artifacts=[artifact_id], required=True,
        )
        dependencies.append(dependency)
    analysis = AgentTask(
        id="task_003", agent=Producer.ExpAgent, kind=AgentKind.advise,
        capability="analyze_results", required=True,
        depends_on=[task.id for task in dependencies],
        input={
            "task_goal": (
                "Compare the two supplied experiment artifacts. State which has "
                "higher accuracy and cite both artifact values. Do not schedule "
                "new experiments."
            ),
        },
    )
    state.tasks.extend([*dependencies, analysis])
    controller = build_controller(config, mock=False)
    controller.planner = SequencePlanner([
        PlannedAction(ActionName.call_exp_agent, {"task_id": analysis.id}),
        PlannedAction(ActionName.finish, {"summary": "fan-in analysis completed"}),
    ])

    observations = _step_until_stop(state, controller, max_steps=2)

    assert analysis.status == TaskStatus.completed
    assert len(analysis.input.get("input_artifacts", [])) == 2
    decision = state.find_artifact(analysis.artifacts[-1])
    assert decision is not None
    evidence = (run_root / decision.path).read_text(encoding="utf-8").lower()
    assert "0.91" in evidence and "0.94" in evidence
    assert state.run.status == RunStatus.completed
    _assert_artifacts_exist(state)
    _assert_parent_links(state, {"expagent"})
    return {"run_id": state.run.run_id, "steps": len(observations),
            "input_artifacts": len(analysis.input["input_artifacts"])}


CASES = {"coding": case_coding, "repro": case_repro,
         "dependency-chain": case_dependency_chain,
         "fan-in-analysis": case_fan_in_analysis,
         "env-reuse": case_env_reuse}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="", help="Optional config YAML; defaults are used when omitted",
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    parser.add_argument(
        "--repro-local-repo", default="",
        help="Use a local repository snapshot for the repro case instead of GitHub",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    selected = list(CASES) if args.case == "all" else [args.case]
    report = {"status": "passed", "cases": {}, "started_at": time.time()}
    try:
        for name in selected:
            started = time.monotonic()
            if name == "repro" and args.repro_local_repo:
                local_repo = Path(args.repro_local_repo).expanduser().resolve()
                if not (local_repo / ".git").is_dir():
                    raise ValueError(
                        f"--repro-local-repo is not a Git worktree: {local_repo}"
                    )
                result = case_repro(config, workspace, local_repo)
            else:
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
