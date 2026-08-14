"""Convert ExpAgent V2 scientific actions into ResAgent tasks.

ExpAgent emits a discriminated union of actions keyed on `capability` (flat,
no `type` or `plan.kind`, and no physical fields). ResAgent owns the physical
resolution — executor routing (via the capability registry), workspace
inference, environment binding, retries and artifact materialization — so this
module only maps the logical action graph onto the internal AgentTask model.
"""

from __future__ import annotations

import os
import re

from ...models import AgentTask, Producer, TaskPriority
from ...task_contracts import resolve_action, task_fingerprint
from ...capabilities import CapabilityError
from .dependency_graph import dependency_graph_issues


def actions_to_tasks(
    actions: list[dict], state, source: str, next_num: int, registry=None,
) -> tuple[list[AgentTask], list[str]]:
    """Convert one validated ExpAgent V2 action graph into ResAgent tasks."""
    issues = dependency_graph_issues(actions)
    if issues:
        return [], issues

    tasks: list[AgentTask] = []
    action_tasks: dict[str, AgentTask] = {}
    pending_dependencies: list[tuple[AgentTask, list[str]]] = []
    actions_by_id = {
        str(action.get("action_id", "")).strip(): action
        for action in actions
        if str(action.get("action_id", "")).strip()
    }

    for index, action in enumerate(actions):
        action_id = str(action.get("action_id", "")).strip()
        dependency_names = [
            str(value).strip() for value in action.get("depends_on", [])
        ]
        capability = str(action.get("capability", "")).strip()

        try:
            agent, kind, canonical = resolve_action(action, registry)
        except (CapabilityError, ValueError) as exc:
            issues.append(str(exc))
            continue

        needs_workspace = capability in {"modify_code", "execute_experiment"}
        workspace_path = (
            infer_workspace_path(
                state, action, dependency_names, action_tasks, actions_by_id,
            )
            if needs_workspace else ""
        )
        task_input = _task_input(action, workspace_path)
        # Fingerprint on logical identity only. Physical fields ResAgent resolves
        # (workspace_path, env) must NOT change the identity across
        # re-consultations, or a follow-up ExpAgent advisory would re-plan an
        # already-completed experiment just because its workspace materialized.
        logical = dict(task_input)
        logical.pop("workspace_path", None)
        logical["project_ref"] = str(action.get("project_ref", "")).strip()
        fingerprint = task_fingerprint(agent, canonical, logical)
        equivalent = state.find_task_by_fingerprint(fingerprint)
        if equivalent is None:
            equivalent = next(
                (item for item in tasks if item.fingerprint == fingerprint), None
            )
        if equivalent is not None:
            if action_id:
                action_tasks[action_id] = equivalent
            continue

        task = AgentTask(
            id=f"task_{next_num + index:03d}",
            source=source,
            agent=agent,
            kind=kind,
            priority=TaskPriority.medium,
            capability=canonical,
            required=bool(action.get("required", True)),
            fingerprint=fingerprint,
            action_id=action_id,
            project_ref=str(action.get("project_ref", "")).strip(),
            input=task_input,
        )
        tasks.append(task)
        pending_dependencies.append((task, dependency_names))
        if action_id:
            action_tasks[action_id] = task

    if issues:
        return [], issues
    for task, dependency_names in pending_dependencies:
        task.depends_on = [
            action_tasks[name].id for name in dependency_names
            if name in action_tasks
        ]
    return tasks, []


def _task_input(action: dict, workspace_path: str) -> dict:
    """Map a flat V2 action onto the AgentTask.input contract."""
    capability = str(action.get("capability", "")).strip()
    objective = str(action.get("objective", "")).strip()
    rationale = str(action.get("rationale", "")).strip()

    input_data: dict = {
        "description": rationale,
        "objective": objective,
        "success_criteria": list(action.get("success_criteria") or []),
        "workspace_path": workspace_path,
    }

    if capability == "modify_code":
        input_data.update({
            "task_goal": objective,
            "constraints": list(action.get("constraints") or []),
            "verify_commands": list(action.get("verify_commands") or []),
            "expected_artifacts": list(action.get("expected_artifacts") or []),
        })
    elif capability in {"reproduce_experiment", "execute_experiment"}:
        input_data.update({
            "experiment_goal": objective,
            "paper_url": str(action.get("paper_url", "")).strip(),
            "repo_url": str(action.get("repo_url", "")).strip(),
            "expected_metrics": list(action.get("expected_metrics") or []),
            "requires_gpu": bool(action.get("requires_gpu", False)),
        })
    elif capability == "analyze_results":
        input_data["task_goal"] = objective
    elif capability == "search_literature":
        input_data["task_goal"] = objective
        input_data["search_query"] = str(action.get("search_query", "")).strip()
    elif capability == "ask_user":
        input_data["question"] = str(action.get("question", "")).strip()

    return input_data


def infer_workspace_path(
    state,
    action: dict,
    dependency_names: list[str],
    action_tasks: dict[str, AgentTask],
    actions_by_id: dict[str, dict],
) -> str:
    """Resolve the operational workspace ResAgent owns (not ExpAgent).

    Precedence: explicit field (tolerated for robustness) → a direct
    dependency's materialized workspace → a local repo path in the research
    goal → an existing task's workspace.
    """
    for key in ("workspace_path", "repo_path"):
        value = str(action.get(key, "")).strip()
        if value:
            return value

    for dependency_name in dependency_names:
        dependency_task = action_tasks.get(dependency_name)
        if dependency_task is not None:
            inherited = (
                dependency_task.input.get("workspace_path")
                or dependency_task.input.get("repo_path", "")
            )
            if inherited:
                return inherited
        dependency_action = actions_by_id.get(dependency_name) or {}
        inherited = (
            str(dependency_action.get("workspace_path", "")).strip()
            or str(dependency_action.get("repo_path", "")).strip()
        )
        if inherited:
            return inherited

    goal = state.run.research_goal if state else ""
    # Strip URLs so only local filesystem paths are considered as workspaces.
    goal_no_urls = re.sub(r"https?://[^\s<>()\[\]{}]+", " ", goal)
    for pattern in [r"(/[^\s,;]+)", r"([A-Za-z]:\\[^\s,;]+)"]:
        match = re.search(pattern, goal_no_urls)
        if match:
            path = match.group(1).rstrip(".")
            if "://" in path or path.startswith("//"):
                continue
            if "." in os.path.basename(path) and not os.path.isdir(path):
                parent = os.path.dirname(path)
                if parent:
                    return parent
            return path

    if state:
        for task in state.tasks:
            for key in ("workspace_path", "repo_path"):
                path = task.input.get(key, "")
                if path:
                    return path
    return ""
