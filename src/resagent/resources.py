"""Run-scoped resource resolution and session binding registration."""

from __future__ import annotations

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

    if task.agent == Producer.CodingAgent:
        if repo and not task.input.get("workspace_path"):
            task.input["workspace_path"] = repo.path
        if not task.input.get("workspace_path") and task.input.get("repo_url"):
            task.input["workspace_path"] = str(layout.project_workspace(task.project_ref))
        if environment:
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
    if environment:
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
            metadata={
                "policy": str(environment.get("policy", "")),
                "audit_artifact": str(environment.get("audit_artifact", "")),
            },
        ))


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
