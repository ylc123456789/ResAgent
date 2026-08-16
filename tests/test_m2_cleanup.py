"""M2-P4: cleanup plan protections and apply routing."""

import json
import os
from pathlib import Path

from resagent.cleanup import apply_cleanup, inspect_resources, plan_cleanup


def _manifest(env_id: str, *, manager="reproagent", state="ready",
              pinned=False, last_used_at=None, prefix="") -> dict:
    return {
        "schema": "ENVIRONMENT_MANIFEST_V1",
        "env_id": env_id,
        "state": state,
        "certification": "experiment",
        "spec_fingerprint": "a" * 64,
        "resolved_fingerprint": "b" * 64,
        "prefix": prefix,
        "manager": manager,
        "created_by": {"module": manager},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
        "last_used_at": last_used_at,
        "pinned": pinned,
    }


def _write_env(root: Path, manifest: dict, *, with_prefix=True) -> Path:
    env_dir = root / "environments" / manifest["env_id"]
    env_dir.mkdir(parents=True, exist_ok=True)
    prefix = root / "conda-envs" / manifest["env_id"]
    if with_prefix:
        prefix.mkdir(parents=True, exist_ok=True)
        (prefix / "pkg.bin").write_bytes(b"x" * 128)
        manifest["prefix"] = str(prefix)
    (env_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return prefix


def _write_lease(root: Path, env_id: str, *, released: bool, pid=None) -> None:
    usage = root / "environments" / env_id / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    lease = {
        "schema": "RESOURCE_LEASE_V1",
        "lease_id": f"lease_r_{env_id}",
        "env_id": env_id,
        "run_id": "r", "task_id": "t",
        "host": "thishost", "pid": pid if pid is not None else os.getpid(),
        "acquired_at": "2026-08-16T00:00:00Z",
        "heartbeat_at": "2026-08-16T00:00:00Z",
        "released_at": "2026-08-16T01:00:00Z" if released else None,
    }
    (usage / f"lease_r_{env_id}.json").write_text(json.dumps(lease), encoding="utf-8")


def test_plan_protections(tmp_path):
    root = tmp_path / "res"
    old = "2020-01-01T00:00:00Z"
    # candidate: expired ready env
    _write_env(root, _manifest("resenv_old_111111111111", last_used_at=old))
    # protected: pinned
    _write_env(root, _manifest("resenv_pin_222222222222", pinned=True, last_used_at=old))
    # protected: active lease (this process, same host)
    _write_env(root, _manifest("resenv_leased_333333333333", last_used_at=old))
    _write_lease(root, "resenv_leased_333333333333", released=False)
    # released lease does NOT protect
    _write_env(root, _manifest("resenv_rel_444444444444", last_used_at=old))
    _write_lease(root, "resenv_rel_444444444444", released=True)
    # protected: recently used
    _write_env(root, _manifest("resenv_recent_555555555555",
                               last_used_at="2999-01-01T00:00:00Z"))
    # never a candidate: prefix outside root
    outside = _manifest("resenv_out_666666666666", last_used_at=old,
                        prefix=str(tmp_path / "elsewhere"))
    _write_env(root, outside, with_prefix=False)

    plan = plan_cleanup(root, min_unused_days=30)
    candidate_ids = {c["env_id"] for c in plan["candidates"]}
    protected = {p["env_id"]: p["reason"] for p in plan["protected"]}

    assert "resenv_old_111111111111" in candidate_ids
    assert "resenv_rel_444444444444" in candidate_ids
    assert protected["resenv_pin_222222222222"] == "pinned"
    assert protected["resenv_leased_333333333333"] == "active_lease"
    assert protected["resenv_recent_555555555555"] == "recently_used"
    assert protected["resenv_out_666666666666"] == "prefix_outside_root"
    assert plan["dry_run"] is True
    assert plan["total_candidate_bytes"] >= 256


def test_plan_max_bytes_truncates_oldest_first(tmp_path):
    root = tmp_path / "res"
    old = "2020-01-01T00:00:00Z"
    _write_env(root, _manifest("resenv_older_111111111111", last_used_at="2019-01-01T00:00:00Z"))
    _write_env(root, _manifest("resenv_newer_222222222222", last_used_at=old))

    full = plan_cleanup(root, min_unused_days=30)
    assert len(full["candidates"]) == 2
    capped = plan_cleanup(root, min_unused_days=30, max_bytes=128)
    assert [c["env_id"] for c in capped["candidates"]] == ["resenv_older_111111111111"]


def test_apply_routes_to_manager_and_rechecks(tmp_path):
    root = tmp_path / "res"
    old = "2020-01-01T00:00:00Z"
    prefix_a = _write_env(root, _manifest("resenv_a_111111111111", last_used_at=old))
    _write_env(root, _manifest("resenv_b_222222222222", manager="unknown_mod",
                               last_used_at=old))

    plan = plan_cleanup(root, min_unused_days=30)
    calls = []

    def fake_delete(root_path, env_id):
        calls.append(env_id)
        # emulate the module-side deletion
        import shutil
        shutil.rmtree(root_path / "environments" / env_id)
        if prefix_a.is_dir():
            shutil.rmtree(prefix_a)
        return {"env_id": env_id, "deleted": True, "reason": ""}

    result = apply_cleanup(root, plan, deleters={"reproagent": fake_delete})
    assert result["deleted"] == ["resenv_a_111111111111"]
    assert calls == ["resenv_a_111111111111"]
    skipped = {s["env_id"]: s["reason"] for s in result["skipped"]}
    assert "manager_unavailable" in skipped["resenv_b_222222222222"]
    assert not (root / "environments" / "resenv_a_111111111111").exists()

    # apply-time re-check: pin added after the plan blocks deletion
    _write_env(root, _manifest("resenv_c_333333333333", last_used_at=old))
    plan = plan_cleanup(root, min_unused_days=30)
    manifest_file = root / "environments" / "resenv_c_333333333333" / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["pinned"] = True
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    result = apply_cleanup(root, plan, deleters={"reproagent": fake_delete})
    skipped = {s["env_id"]: s["reason"] for s in result["skipped"]}
    assert skipped["resenv_c_333333333333"] == "pinned_at_apply"


def test_inspect_resources_summary(tmp_path):
    root = tmp_path / "res"
    _write_env(root, _manifest("resenv_a_111111111111",
                               last_used_at="2020-01-01T00:00:00Z"))
    entries = inspect_resources(root)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["env_id"] == "resenv_a_111111111111"
    assert entry["bytes"] == 128
    assert entry["active_lease"] is False
