"""Convert ExpAgent recommended actions into ResAgent tasks."""

from __future__ import annotations

import os
import re

from ...models import AgentTask, Producer, TaskPriority
from ...task_contracts import (
    normalize_recommended_action,
    required_from_priority,
    task_fingerprint,
)
from .dependency_graph import dependency_graph_issues


def actions_to_tasks(
    actions: list[dict], state, source: str, next_num: int,
) -> tuple[list[AgentTask], list[str]]:
    """Convert one validated ExpAgent action graph into ResAgent tasks."""
    actions = _inject_setup_actions(_upgrade_legacy_action_ids(actions))
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
        normalized_action = dict(action)
        plan = dict(action.get("plan") or {})
        if not plan.get("workspace_path") and not plan.get("repo_path"):
            _inherit_dependency_workspace(
                plan, dependency_names, action_tasks, actions_by_id,
            )
        normalized_action["plan"] = plan
        priority = _priority(action.get("priority", "medium"))

        try:
            normalization_state = state.model_copy(deep=False)
            normalization_state.tasks = [*state.tasks, *tasks]
            agent, kind, capability, plan = normalize_recommended_action(
                normalized_action, normalization_state,
            )
        except ValueError as exc:
            issues.append(str(exc))
            continue

        if (
            agent == Producer.ReproAgent
            and action.get("workspace_intent") == "shared"
            and dependency_names
        ):
            plan.pop("repo_url", None)
            plan.pop("copy_from", None)
            plan.pop("external_repo_path", None)

        workspace_path = infer_workspace_path(state, plan)
        task_input = _task_input(action, plan, workspace_path, dependency_names, agent)
        fingerprint = task_fingerprint(agent, capability, task_input)
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
            priority=priority,
            capability=capability,
            required=required_from_priority(priority, action),
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
        task.depends_on = [action_tasks[name].id for name in dependency_names]
    return tasks, []


def _upgrade_legacy_action_ids(actions: list[dict]) -> list[dict]:
    """Give pre-V1, graph-free decisions stable local IDs for compatibility."""
    graph_keys = ("action_id", "depends_on", "project_ref", "workspace_intent")
    if any(any(action.get(key) for key in graph_keys) for action in actions):
        return [dict(action) for action in actions]
    upgraded = []
    for index, action in enumerate(actions, start=1):
        item = dict(action)
        item["action_id"] = f"legacy_{index:03d}"
        upgraded.append(item)
    return upgraded


def _inject_setup_actions(actions: list[dict]) -> list[dict]:
    """Provision remote coding projects before shared edit/run chains."""
    expanded = [dict(action) for action in actions]
    projects_with_shared_run = {
        str(action.get("project_ref", "")).strip()
        for action in expanded
        if action.get("type") in {"run_task", "repro_task"}
        and action.get("workspace_intent") == "shared"
    }
    result: list[dict] = []
    setup_ids: dict[str, str] = {}
    for action in expanded:
        project_ref = str(action.get("project_ref", "")).strip()
        plan = dict(action.get("plan") or {})
        needs_setup = (
            action.get("type") == "coding_task"
            and bool(plan.get("repo_url"))
            and project_ref in projects_with_shared_run
        )
        if needs_setup and project_ref not in setup_ids:
            setup_id = f"setup_{project_ref}"
            setup_ids[project_ref] = setup_id
            result.append({
                "priority": action.get("priority", "high"),
                "type": "repro_task",
                "action_id": setup_id,
                "depends_on": [],
                "project_ref": project_ref,
                "workspace_intent": "isolated",
                "rationale": "Prepare and audit the repository environment before code changes.",
                "plan": {
                    "kind": "repro_task",
                    "repo_url": plan.get("repo_url", ""),
                    "paper_url": plan.get("paper_url", ""),
                    "experiment_goal": "Prepare and audit the environment only.",
                    "setup_only": True,
                },
            })
        if needs_setup:
            action = dict(action)
            action["depends_on"] = [
                setup_ids[project_ref],
                *[item for item in action.get("depends_on", [])
                  if item != setup_ids[project_ref]],
            ]
            plan = dict(plan)
            plan.pop("repo_url", None)
            action["plan"] = plan
        result.append(action)
    return result


def _inherit_dependency_workspace(
    plan: dict,
    dependency_names: list[str],
    action_tasks: dict[str, AgentTask],
    actions_by_id: dict[str, dict],
) -> None:
    for dependency_name in dependency_names:
        dependency_task = action_tasks.get(dependency_name)
        if dependency_task is not None:
            inherited = (
                dependency_task.input.get("workspace_path")
                or dependency_task.input.get("source_workspace")
            )
            if inherited:
                plan["workspace_path"] = inherited
                return
        dependency_plan = dict(
            (actions_by_id.get(dependency_name) or {}).get("plan") or {}
        )
        inherited = (
            dependency_plan.get("workspace_path") or dependency_plan.get("repo_path")
        )
        if inherited:
            plan["workspace_path"] = inherited
            return


def _priority(raw_priority) -> TaskPriority:
    if isinstance(raw_priority, str):
        return (
            TaskPriority(raw_priority)
            if raw_priority in ("high", "medium", "low")
            else TaskPriority.medium
        )
    if isinstance(raw_priority, (int, float)):
        return TaskPriority.high if raw_priority <= 1 else TaskPriority.medium
    return TaskPriority.medium


def _task_input(
    action: dict,
    plan: dict,
    workspace_path: str,
    dependency_names: list[str],
    agent: Producer,
) -> dict:
    return {
        "description": action.get("rationale", ""),
        "action_type": action.get("type", "ask_user"),
        "task_goal": plan.get("task_goal", ""),
        "paper_url": plan.get("paper_url", ""),
        "repo_url": plan.get("repo_url", ""),
        "experiment_goal": plan.get("experiment_goal", ""),
        "expected_metrics": plan.get("expected_metrics", []),
        "command_goal": plan.get("command_goal", ""),
        "search_query": plan.get("search_query", ""),
        "question": plan.get("question", ""),
        "supersedes_task_id": plan.get("supersedes_task_id", ""),
        "workspace_path": workspace_path,
        "workspace_intent": str(action.get("workspace_intent", "")).strip(),
        "branch": plan.get("branch", ""),
        "env_policy": plan.get("env_policy", ""),
        "env_name": plan.get("env_name", ""),
        "setup_only": bool(plan.get("setup_only", False)),
        "source_workspace": (
            workspace_path
            if dependency_names and agent == Producer.ReproAgent
            else ""
        ),
        "constraints": infer_constraints(plan),
        "verify_commands": infer_verify_commands(plan),
        "expected_artifacts": plan.get("expected_artifacts", []),
        "requires_gpu": plan.get("requires_gpu", False),
        "expected_runtime": plan.get("expected_runtime", ""),
    }


def infer_workspace_path(state, plan: dict) -> str:
    """Infer an operational workspace from the plan, goal, or existing tasks."""
    for key in ("workspace_path", "repo_path"):
        value = plan.get(key, "")
        if value and value.strip():
            return value.strip()

    goal = state.run.research_goal if state else ""
    for pattern in [r"(/[^\s,;]+)", r"([A-Za-z]:\\[^\s,;]+)"]:
        match = re.search(pattern, goal)
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


def infer_constraints(plan: dict) -> list[str]:
    """Generate default constraints if ExpAgent did not provide them."""
    existing = plan.get("constraints", [])
    if existing:
        return list(existing) if isinstance(existing, list) else [existing]
    if plan.get("kind", "") == "coding_task":
        return [
            "Do not change training semantics or model architecture",
            "Only modify files necessary for the stated goal",
        ]
    return []


def infer_verify_commands(plan: dict) -> list[str]:
    """Generate default verification if ExpAgent did not provide it."""
    existing = plan.get("verify_commands", [])
    if existing:
        return list(existing) if isinstance(existing, list) else [existing]
    return ["python -m py_compile *.py"] if plan.get("kind", "") == "coding_task" else []
