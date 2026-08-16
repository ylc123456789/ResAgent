"""M2-P4: safe cleanup of content-addressed environments.

Two steps, always: plan (dry-run output) then apply (explicit only).
ResAgent coordinates the candidate set and the protection set; physical
deletion is routed to the manifest's manager module
(``delete_environment`` entry point) — ResAgent never runs conda/pip or
deletes envs itself.

Protection set (a resource is never a candidate when any holds):
- ``pinned: true``
- an active lease (released_at null and the holder is alive; lease
  holders on other hosts are conservatively treated as alive)
- state ``creating`` while its creation lock holder is alive
- used within ``min_unused_days``
- prefix outside the resource root (containment — never delete)
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from .resources import iter_manifests


# ── helpers ──────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(text) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _pid_alive(pid: int, host: str) -> bool:
    """Liveness is judged by process existence on the same host; holders
    on other hosts are conservatively treated as alive (never by age)."""
    if pid <= 0:
        return False
    if host and host != socket.gethostname():
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _active_leases(root: Path) -> set[str]:
    """env_ids with at least one lease whose holder is (conservatively) alive."""
    active: set[str] = set()
    for lease_path in root.glob("environments/*/usage/lease_*.json"):
        try:
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if lease.get("released_at"):
            continue
        if _pid_alive(int(lease.get("pid", 0) or 0), str(lease.get("host", ""))):
            env_id = str(lease.get("env_id", ""))
            if env_id:
                active.add(env_id)
    return active


def _creation_lock_alive(root: Path, spec_fingerprint: str) -> bool:
    if not spec_fingerprint:
        return False
    lock_path = root / "locks" / f"{spec_fingerprint}.lock"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return _pid_alive(int(lock.get("pid", 0) or 0), str(lock.get("host", "")))


def _contained_prefix(root: Path, manifest: dict) -> str:
    """Return the resolved prefix when it sits under <root>/conda-envs, else ""."""
    prefix = str(manifest.get("prefix", "") or "")
    if not prefix:
        return ""
    envs_root = (root / "conda-envs").resolve()
    resolved = Path(prefix).resolve()
    if resolved == envs_root or envs_root not in resolved.parents:
        return ""
    return str(resolved)


# ── protection (single evaluator used by BOTH plan and apply) ───────

def _protection_reason(
    root: Path, manifest: dict, active: set[str], now: datetime,
    min_unused_days: float,
) -> str:
    """Return "" for a deletion candidate, else the protection reason.

    The SAME evaluation must gate planning and applying — a plan is a
    snapshot, so apply re-runs this exact function per candidate.
    """
    env_id = str(manifest.get("env_id", ""))
    state = str(manifest.get("state", ""))

    if manifest.get("pinned"):
        return "pinned"
    if env_id in active:
        return "active_lease"
    if state == "creating" and _creation_lock_alive(
        root, str(manifest.get("spec_fingerprint", ""))
    ):
        return "creating_active"
    if str(manifest.get("prefix", "")) and not _contained_prefix(root, manifest):
        return "prefix_outside_root"  # containment: never a candidate
    if state == "creating":
        return ""  # dead creator → stale_creating candidate
    if state not in ("ready", "drifted", "failed"):
        return f"unknown_state:{state}"
    last_used = _parse_ts(manifest.get("last_used_at"))
    if last_used is not None and (now - last_used).total_seconds() / 86400.0 < min_unused_days:
        return "recently_used"
    return ""


# ── inspect / plan ───────────────────────────────────────────────────

def inspect_resources(root: str | Path) -> list[dict]:
    """Inventory of every manifest with protection-relevant evidence."""
    root = Path(root)
    active = _active_leases(root)
    entries = []
    for manifest in iter_manifests(str(root)):
        env_id = manifest.get("env_id", "")
        prefix = _contained_prefix(root, manifest)
        entries.append({
            "env_id": env_id,
            "state": manifest.get("state", ""),
            "manager": manifest.get("manager", ""),
            "certification": manifest.get("certification", ""),
            "pinned": bool(manifest.get("pinned")),
            "last_used_at": manifest.get("last_used_at"),
            "active_lease": env_id in active,
            "prefix": prefix,
            "bytes": _dir_size(Path(prefix)) if prefix else 0,
        })
    return entries


def plan_cleanup(
    root: str | Path, *, min_unused_days: float = 30.0, max_bytes: int = 0,
) -> dict:
    """Build the cleanup plan (always a dry-run document)."""
    root = Path(root)
    now = _now()
    active = _active_leases(root)
    candidates: list[dict] = []
    protected: list[dict] = []

    for manifest in iter_manifests(str(root)):
        env_id = str(manifest.get("env_id", ""))
        state = str(manifest.get("state", ""))
        reason = _protection_reason(root, manifest, active, now, min_unused_days)
        if reason:
            protected.append({"env_id": env_id, "reason": reason})
            continue

        prefix = _contained_prefix(root, manifest)
        unused_days: float | None = None
        last_used = _parse_ts(manifest.get("last_used_at"))
        if last_used is not None:
            unused_days = (now - last_used).total_seconds() / 86400.0
        if state == "creating":
            candidate_reason = "stale_creating"
        else:
            candidate_reason = state if state != "ready" else "expired"
        candidates.append({
            "env_id": env_id,
            "manager": str(manifest.get("manager", "")),
            "state": state,
            "reason": candidate_reason,
            "prefix": prefix,
            "bytes": _dir_size(Path(prefix)) if prefix else 0,
            "last_used_at": manifest.get("last_used_at"),
            "unused_days": round(unused_days, 1) if unused_days is not None else None,
        })

    # oldest first; when max_bytes is set, keep only enough to cover it
    candidates.sort(key=lambda c: (c["last_used_at"] is not None,
                                   str(c["last_used_at"] or "")))
    if max_bytes > 0:
        kept: list[dict] = []
        total = 0
        for candidate in candidates:
            if total >= max_bytes:
                break
            kept.append(candidate)
            total += candidate["bytes"]
        candidates = kept

    return {
        "resource_root": str(root),
        "dry_run": True,
        "generated_at": now.isoformat(),
        "min_unused_days": min_unused_days,
        "candidates": candidates,
        "protected": protected,
        "total_candidate_bytes": sum(c["bytes"] for c in candidates),
    }


# ── apply ────────────────────────────────────────────────────────────

def _extend_sys_path(module_path: str) -> None:
    if not module_path:
        return
    src = os.path.join(module_path, "src")
    for path in (src, module_path):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _manager_deleter(manager: str, reproagent_path: str, codingagent_path: str):
    if manager == "reproagent":
        _extend_sys_path(reproagent_path)
        from reproagent.runtime.env_manager import delete_environment
        return delete_environment
    if manager == "codingagent":
        _extend_sys_path(codingagent_path)
        from coding_agent.resources import delete_environment
        return delete_environment
    return None


def apply_cleanup(
    root: str | Path,
    plan: dict,
    *,
    reproagent_path: str = "",
    codingagent_path: str = "",
    deleters: dict | None = None,
) -> dict:
    """Execute a cleanup plan, routing each deletion to the manager module.

    Protection is re-verified per candidate immediately before deletion —
    a plan is a snapshot, and state may have changed since it was built.
    ``deleters`` overrides manager resolution (tests).
    """
    root = Path(root)
    results: list[dict] = []
    now = _now()
    active = _active_leases(root)
    current = {m.get("env_id"): m for m in iter_manifests(str(root))}
    for candidate in plan.get("candidates", []):
        env_id = str(candidate.get("env_id", ""))
        # re-verify the FULL protection set at apply time — a plan is a
        # snapshot, and state may have changed since it was built
        manifest = current.get(env_id)
        if manifest is None:
            results.append({"env_id": env_id, "deleted": False,
                            "reason": "manifest_gone"})
            continue
        reason = _protection_reason(
            root, manifest, active, now,
            plan.get("min_unused_days", 30.0),
        )
        if reason:
            results.append({"env_id": env_id, "deleted": False,
                            "reason": f"{reason}_at_apply"})
            continue
        manager = str(manifest.get("manager", ""))
        deleter = (deleters or {}).get(manager)
        if deleter is None:
            try:
                deleter = _manager_deleter(manager, reproagent_path,
                                           codingagent_path)
            except Exception:
                deleter = None
        if deleter is None:
            results.append({"env_id": env_id, "deleted": False,
                            "reason": f"manager_unavailable:{manager}"})
            continue
        results.append(deleter(root, env_id))
    return {
        "applied_at": _now().isoformat(),
        "results": results,
        "deleted": [r["env_id"] for r in results if r.get("deleted")],
        "skipped": [r for r in results if not r.get("deleted")],
    }
