"""Run-scoped resource resolution and session binding registration."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import AgentKind, AgentTask, Producer, ResearchState, ResourceRef, TaskStatus
from .persistence.sessions import read_session_card
from .persistence.workspace import WorkspaceLayout


def resolve_artifact_path(
    state: ResearchState, artifact_path: str | Path,
) -> Path:
    """Resolve an artifact path using the run root as its stable base."""
    path = Path(artifact_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    run_root = Path(state.run.workspace_dir) / state.run.run_id
    return (run_root / path).resolve()


def materialize_task_bindings(
    state: ResearchState,
    task: AgentTask,
    layout: WorkspaceLayout,
    shared_workspace: str = "auto",
    resources=None,
) -> None:
    """Resolve logical project/dependency references immediately before dispatch."""
    if shared_workspace not in {"auto", "always", "never"}:
        raise ValueError(f"invalid shared_workspace policy: {shared_workspace}")
    materialize_dependency_artifacts(state, task)

    repo = _repo_for_task(state, task)
    dependency_repo = _repo_from_direct_dependencies(state, task)
    environment = _environment_for_task(state, task, repo)
    intent = str(task.input.get("workspace_intent", "")).strip()
    share = shared_workspace == "always" or (
        shared_workspace == "auto" and intent != "isolated"
    )

    # M2: in content_addressed mode, propose a ready manifest env for the
    # task's repo (cross-run). The executing module re-verifies by
    # fingerprint before reuse — this injection is only a candidate.
    # Legacy mode is completely unaffected.
    manifest_env = None
    if (
        resources is not None
        and getattr(resources, "reuse_mode", "legacy") == "content_addressed"
        and getattr(resources, "root", "")
        and not task.input.get("env_name")
    ):
        repo_path = (repo.path if repo else "") or (
            dependency_repo.path if dependency_repo else ""
        ) or str(task.input.get("workspace_path", ""))
        manifest_env = select_environment_manifest(resources.root, repo_path)

    if task.agent == Producer.CodingAgent:
        if repo and not task.input.get("workspace_path"):
            task.input["workspace_path"] = repo.path
        if not task.input.get("workspace_path") and task.input.get("repo_url"):
            task.input["workspace_path"] = str(layout.project_workspace(task.project_ref))
        if manifest_env is not None:
            task.input["env_name"] = manifest_env.get("prefix") or manifest_env["env_id"]
            task.input["_lease_env_id"] = manifest_env["env_id"]
            if not task.input.get("env_policy"):
                task.input["env_policy"] = "reuse_only"
        elif environment:
            if not task.input.get("env_name"):
                task.input["env_name"] = environment.id
            if not task.input.get("env_policy"):
                task.input["env_policy"] = "reuse_only"
        elif not task.input.get("env_policy"):
            task.input["env_policy"] = "auto"
        return

    if task.agent != Producer.ReproAgent:
        return
    if dependency_repo:
        # A completed dependency is the current project state. Initial
        # locators describe where the chain started and must not make a
        # downstream experiment clone stale source code again.
        for key in ("repo_url", "copy_from", "external_repo_path"):
            task.input[key] = ""
        task.input["external_repo_path" if share else "copy_from"] = dependency_repo.path
    elif task.input.get("workspace_path") and not any(task.input.get(key) for key in (
            "repo_url", "copy_from", "external_repo_path")):
        task.input["external_repo_path" if share else "copy_from"] = (
            task.input["workspace_path"]
        )
    elif repo and not any(task.input.get(key) for key in (
            "repo_url", "copy_from", "external_repo_path")):
        task.input["external_repo_path" if share else "copy_from"] = repo.path
    task.input["allow_code_delegation"] = False
    if manifest_env is not None:
        task.input["env_name"] = manifest_env.get("prefix") or manifest_env["env_id"]
        task.input["_lease_env_id"] = manifest_env["env_id"]
    elif environment:
        task.input["env_name"] = environment.id


def materialize_dependency_artifacts(
    state: ResearchState, task: AgentTask,
) -> list[dict[str, str]]:
    """Bind immutable outputs from every direct dependency to ``task``."""
    bindings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for dependency_id in task.depends_on:
        dependency = state.find_task(dependency_id)
        if dependency is None:
            continue
        for artifact_id in dependency.artifacts:
            artifact = state.find_artifact(artifact_id)
            if artifact is None or (dependency_id, artifact.id) in seen:
                continue
            seen.add((dependency_id, artifact.id))
            absolute = resolve_artifact_path(state, artifact.path)
            bindings.append({
                "artifact_id": artifact.id,
                "producer_task_id": dependency_id,
                "type": artifact.type.value,
                "path": str(absolute),
                "summary": artifact.summary,
            })
    task.input["input_artifacts"] = bindings
    return bindings


def register_task_resources(
    state: ResearchState,
    task: AgentTask,
    manifest_path: str = "",
    workspace_path: str = "",
) -> None:
    """Register executor bindings, with a workspace fallback for old cards."""
    card = read_session_card(manifest_path) if manifest_path else None
    bindings = card.get("bindings", {}) if isinstance(card, dict) else {}
    repo = bindings.get("repo") if isinstance(bindings, dict) else None
    environment = bindings.get("environment") if isinstance(bindings, dict) else None

    if isinstance(repo, dict) and repo.get("path"):
        upsert_resource(state, ResourceRef(
            kind="repo",
            id=task.project_ref or f"repo:{task.id}",
            path=str(repo.get("path", "")),
            origin=str(repo.get("origin", "")),
            created_by=task.agent,
            created_task=task.id,
            metadata={
                "commit": str(repo.get("commit", "")),
                "mode": str(repo.get("mode", "")),
            },
        ))
    elif workspace_path:
        upsert_resource(state, ResourceRef(
            kind="repo",
            id=task.project_ref or f"repo:{task.id}",
            path=workspace_path,
            origin=str(task.input.get("repo_url", "local")),
            created_by=task.agent,
            created_task=task.id,
            metadata={"mode": "shared"},
        ))

    if isinstance(environment, dict) and environment.get("name"):
        repo_ref = task.project_ref or _repo_id_for_task(state, task)
        upsert_resource(state, ResourceRef(
            kind="environment",
            id=str(environment.get("name", "")),
            path=str(environment.get("path", "")),
            repo=repo_ref,
            certification=str(environment.get("certification", "")),
            created_by=task.agent,
            created_task=task.id,
            # M2 bindings (ENVIRONMENT_MANIFEST_V1) — empty on legacy cards
            manifest_path=str(environment.get("manifest_path", "")),
            prefix=str(environment.get("prefix", "")),
            spec_fingerprint=str(environment.get("spec_fingerprint", "")),
            resolved_fingerprint=str(environment.get("resolved_fingerprint", "")),
            manager=task.agent.value if isinstance(task.agent, Producer) else str(task.agent),
            last_used_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "policy": str(environment.get("policy", "")),
                "audit_artifact": str(environment.get("audit_artifact", "")),
            },
        ))


# ── M2: content-addressed environment selection (contracts/ENVIRONMENT_*_V1) ──

_CERT_RANK = {"": 0, "none": 0, "verification": 1, "experiment": 2}


def read_manifest(resource_root: str, env_id: str) -> dict | None:
    """Read one ENVIRONMENT_MANIFEST_V1; None when absent or unreadable."""
    path = Path(resource_root) / "environments" / env_id / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _lifecycle_lock_path(resource_root: str | Path, env_id: str) -> Path:
    if not env_id or Path(env_id).name != env_id:
        raise ValueError(f"invalid environment id: {env_id!r}")
    return Path(resource_root) / "locks" / "lifecycle" / f"{env_id}.lock"


def lifecycle_lock_alive(
    resource_root: str | Path,
    env_id: str,
    *,
    malformed_grace_seconds: float = 30.0,
) -> bool:
    """Return whether an environment lifecycle lock has a live owner."""
    path = _lifecycle_lock_path(resource_root, env_id)
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except Exception:
        try:
            return time.time() - path.stat().st_mtime < malformed_grace_seconds
        except OSError:
            return False

    host = str(owner.get("host", ""))
    try:
        pid = int(owner.get("pid", 0) or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if host and host != socket.gethostname():
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def environment_lifecycle_lock(
    resource_root: str | Path,
    env_id: str,
    *,
    timeout_seconds: float = 30.0,
):
    """Serialize lease acquisition and cleanup for one environment."""
    path = _lifecycle_lock_path(resource_root, env_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    owner = {
        "token": token,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }
    deadline = time.monotonic() + max(timeout_seconds, 0.0)

    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(owner, handle)
            break
        except FileExistsError:
            try:
                observed = path.stat()
            except FileNotFoundError:
                continue
            if not lifecycle_lock_alive(resource_root, env_id):
                try:
                    current = path.stat()
                    if (current.st_dev, current.st_ino) == (
                        observed.st_dev, observed.st_ino,
                    ):
                        path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"resource temporarily unavailable: lifecycle lock for {env_id}"
                )
            time.sleep(0.05)

    try:
        yield
    finally:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("token") == token:
                path.unlink()
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass


def iter_manifests(resource_root: str):
    """Yield every readable manifest under the resource root."""
    envs_dir = Path(resource_root) / "environments"
    if not envs_dir.is_dir():
        return
    for manifest_path in sorted(envs_dir.glob("*/manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("env_id"):
            yield data


def _repo_head_commit(repo_path: str) -> str:
    """Best-effort current HEAD commit; "" when not determinable."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path,
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def select_environment_manifest(
    resource_root: str, repo_path: str, min_certification: str = "",
) -> dict | None:
    """Propose a ready, non-drifted env manifest for a repo (cross-run).

    Match key is provenance.repo_path — the orchestrator proposes a
    candidate; the executing module re-verifies by fingerprint and re-audits
    before reuse (the manifest never authorizes reuse by itself). Ranked by
    certification, then most recently used.

    A candidate is only proposed when the repo's current HEAD matches the
    manifest's provenance commit: a moved commit means the spec MAY have
    changed, and a stale candidate must never be injected (the module
    computes the fingerprint fresh in that case).
    """
    if not repo_path:
        return None
    if min_certification and min_certification not in _CERT_RANK:
        return None  # unknown certification vocabulary: fail closed
    want_rank = _CERT_RANK.get(min_certification, 0)
    head = _repo_head_commit(repo_path)
    candidates = []
    for manifest in iter_manifests(resource_root):
        if manifest.get("state") != "ready":
            continue
        provenance = manifest.get("provenance") or {}
        if str(provenance.get("repo_path", "")) != repo_path:
            continue
        manifest_commit = str(provenance.get("repo_commit", ""))
        if head and manifest_commit and head != manifest_commit:
            continue  # repo moved on; do not inject a possibly-stale env
        rank = _CERT_RANK.get(str(manifest.get("certification", "")), 0)
        if rank < want_rank:
            continue
        candidates.append((rank, str(manifest.get("last_used_at") or ""), manifest))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def acquire_lease(
    resource_root: str, env_id: str, run_id: str, task_id: str,
) -> str:
    """Write a lease only while the environment is available for use."""
    if not resource_root or not env_id:
        return ""
    usage_dir = Path(resource_root) / "environments" / env_id / "usage"
    now = datetime.now(timezone.utc).isoformat()
    lease = {
        "schema": "RESOURCE_LEASE_V1",
        "lease_id": f"lease_{run_id}_{task_id}",
        "env_id": env_id,
        "run_id": run_id,
        "task_id": task_id,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": now,
        "heartbeat_at": now,
        "released_at": None,
    }
    try:
        with environment_lifecycle_lock(resource_root, env_id):
            manifest = read_manifest(resource_root, env_id)
            if manifest is None or manifest.get("state") != "ready":
                return ""
            usage_dir.mkdir(parents=True, exist_ok=True)
            path = usage_dir / f"lease_{run_id}_{task_id}.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(lease, indent=2, ensure_ascii=False), encoding="utf-8",
            )
            tmp.replace(path)
            return str(path)
    except (OSError, TimeoutError, ValueError):
        return ""


def release_lease(lease_path: str) -> None:
    """Mark a lease released. Best-effort; never raises."""
    if not lease_path:
        return
    try:
        path = Path(lease_path)
        lease = json.loads(path.read_text(encoding="utf-8"))
        lease["released_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(lease, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def schedule_coding_repair(
    state: ResearchState,
    repro_task: AgentTask,
    coding_issues: list[str],
    workspace_path: str,
) -> AgentTask | None:
    """Create one deterministic CodingAgent repair task for a blocked operator."""
    existing_id = str(repro_task.input.get("_repair_task_id", ""))
    if existing_id and state.find_task(existing_id):
        return None
    repair_count = sum(
        task.source == repro_task.id and task.agent == Producer.CodingAgent
        for task in state.tasks
    )
    if repair_count >= state.budget.max_task_retries:
        return None
    issues = [str(issue).strip() for issue in coding_issues if str(issue).strip()]
    if not issues:
        return None
    repo = _repo_for_task(state, repro_task)
    path = workspace_path or (repo.path if repo else "")
    environment = _environment_for_task(state, repro_task, repo)
    env_name = str(repro_task.input.get("env_name", "")) or (
        environment.id if environment else ""
    )
    if not path or not env_name:
        return None
    task = AgentTask(
        id=f"task_{state.next_task_number():03d}",
        source=repro_task.id,
        agent=Producer.CodingAgent,
        kind=AgentKind.coding_task,
        action_id=f"repair_{repro_task.id}",
        project_ref=repro_task.project_ref,
        input={
            "task_goal": "Resolve experiment-operator blockers:\n- " + "\n- ".join(issues),
            "workspace_path": path,
            "env_policy": "frozen",
            "env_name": env_name,
            "constraints": [
                "Make the smallest code change needed for the stated experiment goal",
                "Do not install, remove, or upgrade dependencies",
            ],
            "verify_commands": [],
            "repairs_task_id": repro_task.id,
        },
    )
    repro_task.input["_repair_task_id"] = task.id
    if task.id not in repro_task.depends_on:
        repro_task.depends_on.append(task.id)
    state.tasks.append(task)
    return task


def resume_repaired_tasks(state: ResearchState, coding_task: AgentTask) -> None:
    """Return blocked operator tasks to pending after their repair completes."""
    for task in state.tasks:
        if task.input.get("_repair_task_id") == coding_task.id and task.status == TaskStatus.blocked:
            task.status = TaskStatus.pending
            task.error = ""
            task.input.pop("_repair_task_id", None)


def upsert_resource(state: ResearchState, resource: ResourceRef) -> None:
    for index, current in enumerate(state.resources):
        if current.kind == resource.kind and current.id == resource.id:
            state.resources[index] = resource
            return
    state.resources.append(resource)


def _repo_for_task(state: ResearchState, task: AgentTask) -> ResourceRef | None:
    if task.project_ref:
        found = next((resource for resource in reversed(state.resources)
                      if resource.kind == "repo" and resource.id == task.project_ref), None)
        if found:
            return found
    for dependency_id in reversed(task.depends_on):
        found = next((resource for resource in reversed(state.resources)
                      if resource.kind == "repo" and resource.created_task == dependency_id), None)
        if found:
            return found
    return None


def _repo_from_direct_dependencies(
    state: ResearchState, task: AgentTask,
) -> ResourceRef | None:
    dependency_ids = set(task.depends_on)
    return next((resource for resource in reversed(state.resources)
                 if resource.kind == "repo"
                 and resource.created_task in dependency_ids), None)


def _environment_for_task(
    state: ResearchState, task: AgentTask, repo: ResourceRef | None,
) -> ResourceRef | None:
    if task.input.get("env_name"):
        return next((resource for resource in reversed(state.resources)
                     if resource.kind == "environment" and resource.id == task.input["env_name"]), None)
    repo_id = repo.id if repo else task.project_ref
    return next((resource for resource in reversed(state.resources)
                 if resource.kind == "environment" and resource.repo == repo_id), None)


def _repo_id_for_task(state: ResearchState, task: AgentTask) -> str:
    repo = _repo_for_task(state, task)
    return repo.id if repo else ""
