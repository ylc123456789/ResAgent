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

from ...models import AgentTask, Producer, TaskPriority, TaskStatus
from ...controller.contracts import resolve_action
from ...controller.tasks import create_task, task_fingerprint
from ...capabilities import CapabilityError
from .dependency_graph import dependency_graph_issues


def actions_to_tasks(
    actions: list[dict], state, source: str, next_num: int, registry,
    analysis_required: bool | None = None,
    supersedes_action_ids: list[str] | None = None,
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

    for action in actions:
        # Optional recommendations are scientific record, not committed work.
        # They remain in scientific_decision.json and are surfaced at finish.
        if not bool(action.get("required", True)):
            continue
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
        # Preserve the advisor's action verbatim before ResAgent threads any
        # runtime binding into it (repo_url below). It is a runtime record, not
        # a second plan.
        scientific_action = dict(action)
        # modify_code with no prior workspace: thread the goal's repo URL so the
        # executor can clone the repo itself. The URL is otherwise lost — ExpAgent
        # emits no physical URL and there is no reproduce task to inherit from.
        if (capability == "modify_code"
                and not str(action.get("repo_url", "")).strip()
                and not workspace_path):
            action = {**action, "repo_url": _extract_repo_url(state.run.research_goal)}
        task_input = _task_input(action, workspace_path, scientific_action=scientific_action)
        # Fingerprint on logical identity only. Physical fields ResAgent resolves
        # (workspace_path, env) and the verbatim action record must NOT change
        # the identity across re-consultations, or a follow-up ExpAgent advisory
        # would re-plan an already-completed experiment just because its
        # workspace materialized.
        logical = dict(task_input)
        logical.pop("workspace_path", None)
        logical.pop("scientific_action", None)
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

        task = create_task(
            state,
            source=source,
            agent=agent,
            kind=kind,
            priority=TaskPriority.medium,
            capability=canonical,
            required=True,
            analysis_required=(
                bool(analysis_required)
                if capability in {"execute_experiment", "reproduce_experiment"}
                and analysis_required is not None
                else None
            ),
            fingerprint=fingerprint,
            action_id=action_id,
            project_ref=str(action.get("project_ref", "")).strip(),
            input=task_input,
            append=False,
            task_number=next_num + len(tasks),
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
    _, replacement_issues = retire_superseded_actions(
        state,
        supersedes_action_ids or [],
        replacement_source=source,
        new_action_ids={task.action_id for task in tasks if task.action_id},
    )
    issues.extend(replacement_issues)
    return tasks, issues


def retire_superseded_actions(
    state,
    action_ids: list[str],
    *,
    replacement_source: str,
    new_action_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Retire only explicitly named, uniquely matched pending actions.

    Decisions are append-only by default. Absence from a new action graph never
    implies cancellation. Ambiguous or unknown ids are reported and left alone
    so task lifecycle changes are never guessed.
    """
    requested = list(dict.fromkeys(value.strip() for value in action_ids if value.strip()))
    if not requested:
        return [], []

    new_ids = new_action_ids or set()
    retired: list[str] = []
    issues: list[str] = []
    for action_id in requested:
        if action_id in new_ids:
            issues.append(
                f"superseded action_id '{action_id}' is also present in the new graph"
            )
            continue
        matching = [
            task for task in state.tasks
            if task.action_id == action_id and task.status == TaskStatus.pending
        ]
        if len(matching) != 1:
            reason = "unknown" if not matching else "ambiguous"
            issues.append(f"{reason} superseded action_id '{action_id}'")
            continue
        task = matching[0]
        task.status = TaskStatus.skipped
        task.input["superseded_by"] = replacement_source
        retired.append(task.id)
    return retired, issues


def _task_input(
    action: dict, workspace_path: str, scientific_action: dict | None = None,
) -> dict:
    """Map a flat V2 action onto the AgentTask.input contract.

    ``scientific_action`` is the advisor's action verbatim, kept as a runtime
    record so future scientific fields survive the flat projection. It is the
    pre-mutation action (before ResAgent threads runtime bindings).
    """
    capability = str(action.get("capability", "")).strip()
    objective = str(action.get("objective", "")).strip()
    rationale = str(action.get("rationale", "")).strip()

    input_data: dict = {
        "scientific_action": (
            scientific_action if scientific_action is not None else dict(action)
        ),
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
            "repo_url": str(action.get("repo_url", "")).strip(),
        })
    elif capability in {"reproduce_experiment", "execute_experiment"}:
        input_data.update({
            "experiment_goal": objective,
            "paper_url": str(action.get("paper_url", "")).strip(),
            "repo_url": str(action.get("repo_url", "")).strip(),
            "expected_metrics": list(action.get("expected_metrics") or []),
            "expected_artifacts": list(action.get("expected_artifacts") or []),
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
    # The (?<!\S) lookbehind requires the path to begin at a token boundary
    # (start of text or after whitespace), so a mid-token slash — as in a
    # relative path like "models/resnet.py" — is not mistaken for an absolute
    # path whose parent would wrongly resolve to "/".
    for pattern in [r"(?<!\S)(/[^\s,;]+)", r"(?<!\S)([A-Za-z]:\\[^\s,;]+)"]:
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


def _extract_repo_url(goal: str) -> str:
    """Return the single repo URL mentioned in the goal, or "" if none/ambiguous.

    Conservative, like workspace inference: only an unambiguous single URL is
    returned, so a goal comparing two repos never picks the wrong one.
    """
    seen: list[str] = []
    for url in re.findall(r"https?://[A-Za-z0-9._~:/?#@!$&'*+,;=%-]+", goal or ""):
        url = url.rstrip(".,;:!?")
        if url and url not in seen:
            seen.append(url)
    return seen[0] if len(seen) == 1 else ""
