#!/usr/bin/env python3
"""Real LLM/GPU acceptance suite for ResAgent and its submodules."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from resagent.config import load_config
from resagent.models import (
    ActionName, AgentKind, AgentTask, Artifact, ArtifactType, Producer,
    RunStatus, TaskStatus,
)
from resagent.orchestrator import build_controller, init_run
from resagent.integrations.module_paths import resolve_all
from resagent.controller.planner import PlannedAction
from resagent.persistence.state import save_state
from resagent.resources import iter_manifests


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


def _step_until_stop(state, controller, max_steps=12, after_step=None):
    observations = []
    try:
        for _ in range(max_steps):
            obs = controller.step(state)
            if after_step is not None:
                after_step(state)
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


def _enforce_bounded_scope(state) -> None:
    """Play the user for scope commitment in the bounded repro acceptance.

    The goal declares exactly one bounded 3-epoch experiment. The advisor
    plans with LLM variance — sometimes adding experiment variants as
    required work, sometimes proposing required follow-ups after analysis.
    In production the user arbitrates scope; in this acceptance the harness
    does, deterministically: keep the advisory, any pre-analysis code patch,
    the FIRST experiment and the FIRST analysis covering it; once that
    analysis has completed, decline every further required task. (skipped is
    a terminal, gate-resolving state in V2.) The "exactly one completed
    experiment" assertion still guards the P4 regression class (redundant or
    misrouted executions) — it just no longer depends on planning variance.
    """
    keep: set[str] = set()
    first_experiment = None
    # A code patch belongs to the committed plan when it precedes the first
    # analysis in creation order (planned up-front to make the bounded
    # experiment runnable) OR when it repairs the kept experiment mid-flight
    # (blocked->repair loop: source is the experiment task id). modify_code
    # proposed in a follow-up wave has a decision as its source and is
    # declined like any other follow-up.
    first_analysis_index = next(
        (i for i, task in enumerate(state.tasks)
         if task.capability == "analyze_results"),
        len(state.tasks),
    )
    for index, task in enumerate(state.tasks):
        if task.agent == Producer.ExpAgent and task.action_id == "initial_consult":
            keep.add(task.id)
        elif task.capability == "modify_code" and (
            index < first_analysis_index
            or (first_experiment is not None and task.source == first_experiment.id)
        ):
            keep.add(task.id)
        elif (task.agent == Producer.ReproAgent
              and task.capability in {"execute_experiment", "reproduce_experiment"}):
            if first_experiment is None:
                first_experiment = task
                keep.add(task.id)
    if first_experiment is not None:
        for task in state.tasks:
            if (task.capability == "analyze_results"
                    and first_experiment.id in task.depends_on):
                keep.add(task.id)
                break
    for task in state.tasks:
        if task.id in keep or not task.required:
            continue
        if task.status in (TaskStatus.pending, TaskStatus.blocked,
                           TaskStatus.failed):
            task.status = TaskStatus.skipped
            task.error = "Declined by acceptance scope (user decision)."


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
    # Same scope arbitration as case_repro: the advisor may keep proposing
    # execute→analyze waves; the harness (as the user) commits to the first
    # experiment + its analysis and declines the rest — the case's outcome
    # must not depend on LLM planning variance.
    observations = _step_until_stop(state, controller, max_steps=10,
                                    after_step=_enforce_bounded_scope)
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
    observations = _step_until_stop(
        state, controller, max_steps=12, after_step=_enforce_bounded_scope,
    )
    completed = [task for task in state.tasks
                 if task.agent == Producer.ReproAgent
                 and task.status == TaskStatus.completed]
    assert completed, "no ReproAgent task completed"
    # V2 scientific-closure invariant (§H.1.4): exactly one experiment, then
    # exactly one analysis — no task/call inflation.
    assert len(completed) == 1, (
        f"expected exactly 1 ReproAgent experiment, got {len(completed)}"
    )
    # Count EXECUTED analysis only. Optional follow-up proposals the advisor
    # marks required=False stay pending and are legitimate — they are
    # proposals, not executed work (§H.1.4 is an execution invariant).
    analysis = [task for task in state.tasks
                if task.capability == "analyze_results"
                and task.status == TaskStatus.completed]
    assert len(analysis) == 1, (
        f"expected exactly 1 executed analyze_results task, got {len(analysis)}"
    )
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
    # Local fixture repo (same pattern as case_dependency_chain): this case
    # verifies environment-namespace reuse, which must not depend on GitHub
    # reachability — the server's GitHub egress is intermittently blocked.
    repo = workspace / "fixtures" / f"envreuse-{int(time.time())}"
    repo.mkdir(parents=True, exist_ok=False)
    (repo / "README.md").write_text(
        "# Env-reuse fixture\n\nRun `python train.py --epochs N`.\n",
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    (repo / "train.py").write_text(
        "import argparse, time\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--epochs', type=int, default=1)\n"
        "args = p.parse_args()\n"
        "start = time.time()\n"
        "for epoch in range(1, args.epochs + 1):\n"
        "    acc = 0.90 + 0.02 * epoch\n"
        "    print(f'Epoch {epoch} | Loss {1.0 / epoch:.4f} | Test Acc {acc:.4f}')\n"
        "print(f'Final Test Acc {0.90 + 0.02 * args.epochs:.4f} | "
        "Runtime {time.time() - start:.2f}s')\n",
        encoding="utf-8",
    )
    for command in (["git", "init"],
                    ["git", "config", "user.email", "acceptance@example.com"],
                    ["git", "config", "user.name", "Acceptance Test"],
                    ["git", "add", "."],
                    ["git", "commit", "-m", "fixture"]):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)

    state = init_run(
        "Run two bounded experiments in one project environment.",
        workspace_root=str(workspace / "runs"), config=config,
    )
    # Synthetic scenario: fixed task graph replaces the seeded advisory task.
    state.tasks.clear()
    # Engineering scenario (environment namespace sharing), no scientific
    # analysis is part of the acceptance — exempt from the coverage gate.
    state.analysis_required = False
    for index, epochs in enumerate((1, 2), start=1):
        state.tasks.append(AgentTask(
            id=f"task_{index:03d}", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task, capability="execute_experiment",
            required=True,
            input={
                "repo_url": str(repo),
                "experiment_goal": (
                    "Run train.py with "
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
    # Synthetic scenario: fixed task graph replaces the seeded advisory task.
    state.tasks.clear()
    # Engineering verification (patch + verify), not scientific closure: the
    # finish gate must not demand analysis coverage for the verify run.
    state.analysis_required = False
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
    # Synthetic scenario: fixed task graph replaces the seeded advisory task.
    # analysis_required stays True — the coverage gate is satisfied by the
    # analysis task below (it depends on both seeded experiments).
    state.tasks.clear()
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

    # Step 1: run the analysis (the fan-in binding this case verifies).
    observations = []
    obs = controller.step(state)
    save_state(state)
    observations.append(obs)
    assert obs.result == "ok", f"analysis failed: {obs.detail}"

    # Play the user. A real advisor may legitimately propose follow-up
    # experiments even when asked not to — that is its job. Scope commitment
    # is the user's call, so the harness declines any new required work here
    # (V2 semantics: skipped is a terminal state; the finish gate treats it
    # as resolved). This keeps the case deterministic without suppressing
    # the advisor's proposals.
    for task in state.tasks:
        if task.required and task.status in (
            TaskStatus.pending, TaskStatus.blocked, TaskStatus.failed,
        ):
            task.status = TaskStatus.skipped
            task.error = "Declined by acceptance scope (user decision)."

    # Step 2: finish.
    obs = controller.step(state)
    save_state(state)
    observations.append(obs)
    assert obs.action == ActionName.finish and obs.result == "ok", (
        f"finish failed: {obs.detail}"
    )

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


def case_m2_env_reuse(config, workspace: Path) -> dict:
    """M2-P5: content-addressed env lifecycle on real conda.

    Scenarios (§11 matrix):
    1. first run creates the env (manifest ready, audit recorded);
    2. a NEW run with the same spec reuses it — zero install;
    3. a dependency change must create a second env;
    4. manual drift must be detected — no blind reuse.
    """
    import copy as _copy

    repo = workspace / "fixtures" / f"m2-{int(time.time())}"
    repo.mkdir(parents=True, exist_ok=False)
    (repo / "README.md").write_text("# M2 fixture\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    (repo / "train.py").write_text(
        "import sys, time\n"
        "start = time.time()\n"
        "print(f'Test Acc 0.9500 | Python {sys.version_info.major}.{sys.version_info.minor}')\n"
        "print(f'Runtime {time.time() - start:.2f}s')\n",
        encoding="utf-8",
    )
    for command in (["git", "init"],
                    ["git", "config", "user.email", "acceptance@example.com"],
                    ["git", "config", "user.name", "Acceptance Test"],
                    ["git", "add", "."],
                    ["git", "commit", "-m", "fixture"]):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)

    cfg = _copy.copy(config)
    cfg.resources = _copy.copy(config.resources)
    cfg.resources.root = str(workspace / "m2-resources")
    cfg.resources.reuse_mode = "content_addressed"
    root = Path(cfg.resources.root)

    def run_once(tag: str, finish: bool = True) -> object:
        state = init_run(
            f"M2 content-addressed env reuse ({tag})",
            workspace_root=str(workspace / "runs"), config=cfg,
        )
        state.tasks.clear()  # synthetic scenario (see case_dependency_chain)
        state.analysis_required = False  # engineering scenario
        state.tasks.append(AgentTask(
            id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
            capability="execute_experiment", required=True,
            project_ref="m2proj",
            input={
                # shared local repo: the manifest provenance key
                "workspace_path": str(repo),
                "experiment_goal": "Run train.py; report accuracy and runtime.",
            },
        ))
        controller = build_controller(cfg, mock=False)
        if finish:
            controller.planner = SequencePlanner([
                PlannedAction(ActionName.call_repro_agent, {"task_id": "task_001"}),
                PlannedAction(ActionName.finish, {"summary": f"m2 {tag} done"}),
            ])
            _step_until_stop(state, controller, max_steps=2)
        else:
            # drift scenario: one step; a blocked/error observation is the
            # expected fail-closed answer, not a case failure
            controller.planner = SequencePlanner([
                PlannedAction(ActionName.call_repro_agent, {"task_id": "task_001"}),
            ])
            controller.step(state)
            save_state(state)
        return state

    manifests = lambda: {
        m["env_id"]: m
        for m in iter_manifests(str(root))
    }

    # 1. first run — create
    state1 = run_once("create")
    assert state1.run.status == RunStatus.completed
    after_first = manifests()
    assert len(after_first) == 1, f"expected 1 manifest, got {sorted(after_first)}"
    env_id, manifest = next(iter(after_first.items()))
    assert manifest["state"] == "ready", manifest
    assert manifest.get("resolved_fingerprint"), "no resolved fingerprint recorded"

    # 2. second run, same spec — reuse, zero install
    state2 = run_once("reuse")
    assert state2.run.status == RunStatus.completed
    after_second = manifests()
    assert len(after_second) == 1, (
        f"reuse created a new env: {sorted(after_second)}"
    )
    bindings2 = state2.tasks[0].input.get("env_name", "")
    assert env_id in bindings2 or manifest["prefix"] in bindings2, (
        f"second run did not bind the existing env: {bindings2}"
    )

    # 3. dependency change — must create a second env
    (repo / "requirements.txt").write_text("numpy\nsix\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add six"], cwd=repo,
                   check=True, capture_output=True)
    state3 = run_once("spec-changed")
    assert state3.run.status == RunStatus.completed
    after_third = manifests()
    assert len(after_third) == 2, (
        f"spec change must create a new env, got {sorted(after_third)}"
    )

    # 4. manual drift — blind reuse must be refused. The drift target must
    # be the env a fourth run would actually reuse: the CURRENT spec's env
    # (stage 3), not the stage-1 one. Uninstalling a spec-required package
    # changes the resolved inventory offline (no network needed).
    env_b_id, manifest_b = next(
        (eid, m) for eid, m in after_third.items() if eid != env_id
    )
    prefix_b = manifest_b["prefix"]
    drift = subprocess.run(
        [str(Path(prefix_b) / "bin" / "python"), "-m", "pip",
         "uninstall", "--quiet", "-y", "six"],
        capture_output=True, text=True, timeout=300,
    )
    assert drift.returncode == 0, (
        f"drift injection pip uninstall failed: {drift.stderr[-300:]}"
    )
    state4 = run_once("drifted", finish=False)
    drifted = manifests().get(env_b_id, {})
    assert drifted.get("state") == "drifted", (
        f"drifted env must be marked drifted, got {drifted.get('state')}"
    )
    assert state4.tasks[0].status != TaskStatus.completed, (
        "blind reuse of a drifted env must not complete"
    )

    return {
        "env_created": env_id,
        "envs_total": len(after_third),
        "drift_detected": True,
        "runs": [state1.run.run_id, state2.run.run_id,
                 state3.run.run_id, state4.run.run_id],
    }


def case_m2_cert_upgrade(config, workspace: Path) -> dict:
    """M2-P5: CodingAgent verification env upgraded by reproagent to experiment.

    The cross-module chain (§6.3): CodingAgent (auto) creates a
    verification-level env for a repo; a later ReproAgent experiment on the
    SAME repo must find it via the manifest, verify fingerprint parity,
    audit it, and upgrade its certification to experiment — one env, no
    second creation.
    """
    import copy as _copy

    repo = workspace / "fixtures" / f"m2cert-{int(time.time())}"
    repo.mkdir(parents=True, exist_ok=False)
    (repo / "README.md").write_text("# M2 cert-upgrade fixture\n", encoding="utf-8")
    # python pinned here so BOTH modules compute the same python identity
    (repo / "environment.yml").write_text(
        "dependencies:\n  - python=3.10\n", encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    (repo / "train.py").write_text(
        "print('Test Acc 0.9500')\n", encoding="utf-8",
    )
    for command in (["git", "init"],
                    ["git", "config", "user.email", "acceptance@example.com"],
                    ["git", "config", "user.name", "Acceptance Test"],
                    ["git", "add", "."],
                    ["git", "commit", "-m", "fixture"]):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)

    cfg = _copy.copy(config)
    cfg.resources = _copy.copy(config.resources)
    cfg.resources.root = str(workspace / "m2cert-resources")
    cfg.resources.reuse_mode = "content_addressed"
    root = Path(cfg.resources.root)

    def run_once(tag: str, tasks: list) -> object:
        state = init_run(
            f"M2 certification upgrade ({tag})",
            workspace_root=str(workspace / "runs"), config=cfg,
        )
        state.tasks.clear()  # synthetic scenario
        state.analysis_required = False  # engineering scenario
        state.tasks.extend(tasks)
        controller = build_controller(cfg, mock=False)
        controller.planner = SequencePlanner([
            PlannedAction(
                ActionName.call_coding_agent
                if t.agent == Producer.CodingAgent else ActionName.call_repro_agent,
                {"task_id": t.id},
            )
            for t in tasks
        ] + [PlannedAction(ActionName.finish, {"summary": f"{tag} done"})])
        _step_until_stop(state, controller, max_steps=len(tasks) + 1)
        return state

    # stage 1: CodingAgent creates the verification env for this repo
    coding = AgentTask(
        id="task_001", agent=Producer.CodingAgent, kind=AgentKind.coding_task,
        capability="modify_code", required=True, project_ref="m2cert",
        input={
            "workspace_path": str(repo),
            "task_goal": "Add a '# verified' comment line at the top of train.py.",
            "verify_commands": ["python train.py"],
            "env_policy": "auto",
        },
    )
    state1 = run_once("verify", [coding])
    assert state1.run.status == RunStatus.completed
    manifests = {m["env_id"]: m for m in iter_manifests(str(root))}
    assert len(manifests) == 1, f"expected 1 env, got {sorted(manifests)}"
    env_id, manifest = next(iter(manifests.items()))
    assert manifest["certification"] == "verification", manifest["certification"]
    assert manifest["manager"] == "codingagent"

    # stage 2: reproagent experiment on the SAME repo must reuse + upgrade it
    repro = AgentTask(
        id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        capability="execute_experiment", required=True, project_ref="m2cert",
        input={
            "workspace_path": str(repo),
            "experiment_goal": "Run train.py; report output.",
        },
    )
    state2 = run_once("upgrade", [repro])
    assert state2.run.status == RunStatus.completed
    after = {m["env_id"]: m for m in iter_manifests(str(root))}
    assert len(after) == 1, f"upgrade must not create a new env: {sorted(after)}"
    upgraded = after[env_id]
    assert upgraded["certification"] == "experiment", (
        f"expected experiment certification, got {upgraded['certification']}"
    )
    assert any(a.get("level") == "experiment" and a.get("outcome") == "pass"
               for a in upgraded.get("audits", [])), "no experiment-level audit"

    return {"env_id": env_id, "upgraded_to": upgraded["certification"],
            "runs": [state1.run.run_id, state2.run.run_id]}


CASES = {"coding": case_coding, "repro": case_repro,
         "dependency-chain": case_dependency_chain,
         "fan-in-analysis": case_fan_in_analysis,
         "env-reuse": case_env_reuse,
         "m2-env-reuse": case_m2_env_reuse,
         "m2-cert-upgrade": case_m2_cert_upgrade}


def _git_metadata(path: Path) -> dict:
    """Return auditable Git identity for one acceptance dependency."""
    requested_path = path.resolve()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=requested_path, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    repository_root = git("rev-parse", "--show-toplevel")
    return {
        "path": repository_root or str(requested_path),
        "commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(git("status", "--porcelain")),
        "remote": git("remote", "get-url", "origin"),
    }


def _acceptance_provenance(config, args, workspace: Path) -> dict:
    modules = resolve_all(
        cli_expagent=config.cmd_expagent,
        cli_codingagent=config.cmd_codingagent,
        cli_reproagent=config.cmd_reproagent,
        config_expagent=config.agents.expagent,
        config_codingagent=config.agents.codingagent,
        config_reproagent=config.agents.reproagent,
    )
    repositories = {
        "ResAgent": _git_metadata(Path(__file__).resolve().parents[1]),
    }
    for name, path_text in (
        ("ExpAgent", modules.expagent.path),
        ("CodingAgent", modules.codingagent.path),
        ("reproagent", modules.reproagent.path),
    ):
        if path_text and Path(path_text).exists():
            repositories[name] = _git_metadata(Path(path_text))
    return {
        "repositories": repositories,
        "config_path": str(Path(args.config).expanduser().resolve()) if args.config else "",
        "model": config.llm.model,
        "api_base": config.llm.api_base,
        "mirror_profile": config.policy.repro_mirror_profile,
        "workspace": str(workspace),
        "repro_local_repo": (
            str(Path(args.repro_local_repo).expanduser().resolve())
            if args.repro_local_repo else ""
        ),
        "dataset_cache": config.policy.repro_dataset_cache,
    }


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
    report = {
        "status": "passed",
        "cases": {},
        "started_at": time.time(),
        "provenance": _acceptance_provenance(config, args, workspace),
    }
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
