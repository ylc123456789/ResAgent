"""M2-P3: content-addressed resource selection, leases, binding recovery.

Legacy mode must be byte-identical to pre-M2 behavior; content-addressed
mode engages only when reuse_mode="content_addressed" AND a root is set.
"""

import json
from pathlib import Path

from resagent.config import ResourcesConfig
from resagent.controller import Controller
from resagent.controller.planner import PlannedAction, Planner
from resagent.models import (
    ActionName, AgentKind, AgentTask, Artifact, ArtifactType, Producer,
    TaskStatus,
)
from resagent.persistence.state import init_state
from resagent.resources import (
    acquire_lease, iter_manifests, read_manifest, register_task_resources,
    release_lease, select_environment_manifest,
)
from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter


def _manifest(env_id: str, repo_path: str, *, state: str = "ready",
              certification: str = "verification", last_used: str = "") -> dict:
    return {
        "schema": "ENVIRONMENT_MANIFEST_V1",
        "env_id": env_id,
        "state": state,
        "certification": certification,
        "spec_fingerprint": "a" * 64,
        "resolved_fingerprint": "b" * 64,
        "prefix": f"/envs/{env_id}",
        "manager": "reproagent",
        "created_by": {"module": "reproagent"},
        "created_at": "2026-08-16T00:00:00Z",
        "updated_at": "2026-08-16T00:00:00Z",
        "pinned": False,
        "provenance": {"repo_path": repo_path, "repo_origin": "local",
                       "repo_commit": "abc"},
        "last_used_at": last_used,
    }


def _write_manifest(root: Path, manifest: dict) -> None:
    env_dir = root / "environments" / manifest["env_id"]
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )


def _resources(root: Path, mode: str = "content_addressed") -> ResourcesConfig:
    return ResourcesConfig(root=str(root), reuse_mode=mode)


# ── manifest IO + selection ──────────────────────────────────────────

def test_manifest_roundtrip_and_iteration(tmp_path):
    _write_manifest(tmp_path, _manifest("resenv_a_111111111111", "/repo/a"))
    found = read_manifest(str(tmp_path), "resenv_a_111111111111")
    assert found is not None and found["state"] == "ready"
    assert read_manifest(str(tmp_path), "resenv_missing_000000000000") is None
    assert len(list(iter_manifests(str(tmp_path)))) == 1
    assert list(iter_manifests(str(tmp_path / "nonexistent"))) == []


def test_selection_requires_ready_and_repo_match(tmp_path):
    _write_manifest(tmp_path, _manifest("resenv_a_111111111111", "/repo/a"))
    _write_manifest(tmp_path, _manifest("resenv_b_222222222222", "/repo/a",
                                        state="drifted"))
    _write_manifest(tmp_path, _manifest("resenv_c_333333333333", "/repo/other"))

    found = select_environment_manifest(str(tmp_path), "/repo/a")
    assert found is not None and found["env_id"] == "resenv_a_111111111111"
    assert select_environment_manifest(str(tmp_path), "/repo/none") is None
    assert select_environment_manifest(str(tmp_path), "") is None


def test_selection_skips_stale_candidate_after_commit_moves(tmp_path):
    """A repo whose HEAD moved past the manifest's provenance commit gets no
    injection — the spec may have changed (cloud M2-P5 stage-3 lesson)."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    for cmd in (["git", "init"], ["git", "config", "user.email", "t@e.c"],
                ["git", "config", "user.name", "t"], ["git", "add", "."],
                ["git", "commit", "-m", "v1"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    commit_v1 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    root = tmp_path / "res"
    manifest = _manifest("resenv_a_111111111111", str(repo))
    manifest["provenance"]["repo_commit"] = commit_v1
    _write_manifest(root, manifest)

    # same commit → candidate proposed
    found = select_environment_manifest(str(root), str(repo))
    assert found is not None

    # commit moves → candidate withheld (module recomputes the fingerprint)
    (repo / "requirements.txt").write_text("numpy\nsix\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=repo,
                   check=True, capture_output=True)
    assert select_environment_manifest(str(root), str(repo)) is None


def test_selection_ranks_certification_then_recency(tmp_path):
    _write_manifest(tmp_path, _manifest("resenv_old_111111111111", "/repo/a",
                                        certification="experiment",
                                        last_used="2026-08-01T00:00:00Z"))
    _write_manifest(tmp_path, _manifest("resenv_new_222222222222", "/repo/a",
                                        certification="verification",
                                        last_used="2026-08-15T00:00:00Z"))
    found = select_environment_manifest(str(tmp_path), "/repo/a")
    assert found["env_id"] == "resenv_old_111111111111"  # experiment wins
    found = select_environment_manifest(str(tmp_path), "/repo/a",
                                        min_certification="experiment")
    assert found["env_id"] == "resenv_old_111111111111"
    assert select_environment_manifest(str(tmp_path), "/repo/a",
                                       min_certification="nonexistent") is None


# ── binding recovery ─────────────────────────────────────────────────

def test_register_recovers_m2_fields(tmp_path):
    card = tmp_path / "session.yaml"
    card.write_text(
        "bindings:\n"
        "  environment:\n"
        "    name: resenv_x_111111111111\n"
        "    certification: experiment\n"
        "    manifest_path: /root/environments/resenv_x_111111111111/manifest.json\n"
        "    prefix: /envs/resenv_x_111111111111\n"
        "    spec_fingerprint: " + "a" * 64 + "\n"
        "    resolved_fingerprint: " + "b" * 64 + "\n",
        encoding="utf-8",
    )
    state = init_state("m2-reg", str(tmp_path), "goal")
    task = AgentTask(id="task_001", agent=Producer.ReproAgent,
                     kind=AgentKind.repro_task, project_ref="proj")
    state.tasks.append(task)
    register_task_resources(state, task, str(card))
    env = next(r for r in state.resources if r.kind == "environment")
    assert env.manifest_path.endswith("manifest.json")
    assert env.prefix == "/envs/resenv_x_111111111111"
    assert env.spec_fingerprint == "a" * 64
    assert env.manager == "ReproAgent"
    assert env.last_used_at


def test_register_tolerates_legacy_card(tmp_path):
    card = tmp_path / "session.yaml"
    card.write_text(
        "bindings:\n  environment:\n    name: repro_legacy\n"
        "    certification: none\n",
        encoding="utf-8",
    )
    state = init_state("m2-legacy", str(tmp_path), "goal")
    task = AgentTask(id="task_001", agent=Producer.ReproAgent,
                     kind=AgentKind.repro_task)
    state.tasks.append(task)
    register_task_resources(state, task, str(card))
    env = next(r for r in state.resources if r.kind == "environment")
    assert env.id == "repro_legacy"
    assert env.manifest_path == "" and env.spec_fingerprint == ""


# ── lease lifecycle ──────────────────────────────────────────────────

def test_lease_acquire_and_release(tmp_path):
    _write_manifest(tmp_path, _manifest("resenv_a_111111111111", "/repo/a"))
    path = acquire_lease(str(tmp_path), "resenv_a_111111111111",
                         "res-1", "task_001")
    assert path
    lease = json.loads(Path(path).read_text(encoding="utf-8"))
    assert lease["schema"] == "RESOURCE_LEASE_V1"
    assert lease["released_at"] is None
    assert lease["pid"] > 0 and lease["host"]

    release_lease(path)
    lease = json.loads(Path(path).read_text(encoding="utf-8"))
    assert lease["released_at"] is not None

    release_lease("")  # no-op, never raises
    assert acquire_lease("", "env", "run", "task") == ""


def test_lease_rejects_non_ready_environment(tmp_path):
    manifest = _manifest("resenv_a_111111111111", "/repo/a")
    manifest["state"] = "deleting"
    _write_manifest(tmp_path, manifest)

    path = acquire_lease(
        str(tmp_path), "resenv_a_111111111111", "res-1", "task_001",
    )

    assert path == ""
    usage = tmp_path / "environments" / manifest["env_id"] / "usage"
    assert not usage.exists()


# ── dispatch-time injection ──────────────────────────────────────────

class _FakeRepro:
    def __init__(self, artifact_path: Path):
        self._artifact_path = artifact_path

    def execute(self, task, layout, attempt_number=1):
        self._artifact_path.write_text("ok", encoding="utf-8")
        return {
            "artifact": Artifact(id="repro_result_1",
                                 type=ArtifactType.repro_result,
                                 producer=Producer.ReproAgent,
                                 path=str(self._artifact_path), summary="ok"),
            "outcome": "completed",
            "raw": {"summary": "ok"},
            "workspace_path": "",
        }


def _controller(resources, fake_repro):
    return Controller(
        planner=Planner(mock=True),
        expagent=ExpAgentAdapter(mock=True),
        codingagent=CodingAgentAdapter(mock=True),
        reproagent=fake_repro,
        resources=resources,
    )


def _repo_task(state, repo_path):
    from resagent.models import ResourceRef
    state.resources.append(ResourceRef(
        kind="repo", id="proj", path=repo_path,
        created_by=Producer.ReproAgent, created_task="task_000",
    ))
    task = AgentTask(
        id="task_001", agent=Producer.ReproAgent, kind=AgentKind.repro_task,
        capability="execute_experiment", project_ref="proj",
        input={"experiment_goal": "run it"},
    )
    state.tasks.append(task)
    return task


def test_content_addressed_injection_and_lease(tmp_path):
    root = tmp_path / "res"
    _write_manifest(root, _manifest("resenv_a_111111111111", "/repo/a"))
    state = init_state("m2-dispatch", str(tmp_path), "goal")
    task = _repo_task(state, "/repo/a")

    ctrl = _controller(_resources(root), _FakeRepro(tmp_path / "result.md"))
    from resagent.controller.planner import PlannedAction as PA
    ctrl.planner = type("P", (), {
        "choose_action": lambda self, s: PA(ActionName.call_repro_agent,
                                            {"task_id": "task_001"}),
        "classify_failure": lambda self, t, e: {"category": "unknown"},
    })()
    obs = ctrl.step(state)

    assert obs.result == "ok"
    assert task.input["env_name"] == "/envs/resenv_a_111111111111"
    assert task.input["_lease_env_id"] == "resenv_a_111111111111"
    # lease was acquired and released around execution
    leases = list((root / "environments" / "resenv_a_111111111111"
                   / "usage").glob("lease_*.json"))
    assert len(leases) == 1
    lease = json.loads(leases[0].read_text(encoding="utf-8"))
    assert lease["released_at"] is not None


def test_dispatch_does_not_run_without_required_lease(tmp_path):
    root = tmp_path / "res"
    env_id = "resenv_a_111111111111"
    manifest = _manifest(env_id, "/repo/a", state="deleting")
    _write_manifest(root, manifest)
    state = init_state("m2-lease-race", str(tmp_path), "goal")
    task = _repo_task(state, "/repo/a")
    task.input.update({
        "_lease_env_id": env_id,
        "env_name": manifest["prefix"],
    })
    result_path = tmp_path / "must-not-run.md"

    ctrl = _controller(_resources(root), _FakeRepro(result_path))
    ctrl.planner = type("P", (), {
        "choose_action": lambda self, s: PlannedAction(
            ActionName.call_repro_agent, {"task_id": "task_001"},
        ),
        "classify_failure": lambda self, t, e: {"category": "transient"},
    })()
    obs = ctrl.step(state)

    assert obs.result == "error"
    assert "resource temporarily unavailable" in obs.detail
    assert task.status == TaskStatus.pending
    assert not result_path.exists()


def test_legacy_mode_ignores_manifests(tmp_path):
    root = tmp_path / "res"
    _write_manifest(root, _manifest("resenv_a_111111111111", "/repo/a"))
    state = init_state("m2-legacy-dispatch", str(tmp_path), "goal")
    task = _repo_task(state, "/repo/a")

    ctrl = _controller(_resources(root, mode="legacy"), _FakeRepro(tmp_path / "result.md"))
    from resagent.controller.planner import PlannedAction as PA
    ctrl.planner = type("P", (), {
        "choose_action": lambda self, s: PA(ActionName.call_repro_agent,
                                            {"task_id": "task_001"}),
        "classify_failure": lambda self, t, e: {"category": "unknown"},
    })()
    obs = ctrl.step(state)

    assert obs.result == "ok"
    assert "env_name" not in task.input
    assert "_lease_env_id" not in task.input
    assert not (root / "environments" / "resenv_a_111111111111"
                / "usage").exists()
