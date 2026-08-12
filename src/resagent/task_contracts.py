"""Deterministic contracts for module routing and run lifecycle checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .models import (
    ActionName, AgentKind, Producer, ResearchState, RunStatus,
    TaskPriority, TaskStatus,
)


TERMINAL_RUN_STATUSES = {RunStatus.completed, RunStatus.failed}
UNRESOLVED_TASK_STATUSES = {
    TaskStatus.pending, TaskStatus.running, TaskStatus.failed,
    TaskStatus.blocked, TaskStatus.needs_user_input,
}


@dataclass(frozen=True)
class FinishCheck:
    allowed: bool
    reason: str = ""
    task_ids: tuple[str, ...] = ()


def action_for_agent(agent: Producer) -> ActionName | None:
    """Return the only dispatch action allowed for an executor."""
    return {
        Producer.ExpAgent: ActionName.call_exp_agent,
        Producer.CodingAgent: ActionName.call_coding_agent,
        Producer.ReproAgent: ActionName.call_repro_agent,
    }.get(agent)


def dependencies_satisfied(task, state: ResearchState) -> bool:
    """Return whether every prerequisite task completed or was skipped."""
    for task_id in task.depends_on:
        dependency = state.find_task(task_id)
        if dependency is None or dependency.status not in {
            TaskStatus.completed, TaskStatus.skipped,
        }:
            return False
    return True


def allowed_action_candidates(state: ResearchState) -> list[dict[str, Any]]:
    """Build exact actions the planner may choose in the current state."""
    if state.run.status in TERMINAL_RUN_STATUSES:
        return []
    if state.pending_question is not None or state.run.status == RunStatus.paused:
        return [{"action": ActionName.ask_user.value, "mode": "await_answer"}]

    candidates: list[dict[str, Any]] = []
    for task in state.tasks:
        if task.status not in (TaskStatus.pending, TaskStatus.failed, TaskStatus.blocked):
            continue
        if not dependencies_satisfied(task, state):
            continue
        action = (
            ActionName.ask_user
            if task.agent == Producer.ResAgent and task.kind == AgentKind.ask_user
            else action_for_agent(task.agent)
        )
        if action is not None:
            candidates.append({"action": action.value, "task_id": task.id})

    if not state.artifacts:
        candidates.append({"action": ActionName.call_exp_agent.value,
                           "mode": "initial_consult"})
    candidates.append({"action": ActionName.ask_user.value})
    if validate_finish(state).allowed:
        candidates.append({"action": ActionName.finish.value})
    return candidates


def validate_finish(state: ResearchState) -> FinishCheck:
    """Check state invariants before allowing a successful finish."""
    if state.pending_question is not None or state.run.status == RunStatus.paused:
        return FinishCheck(False, "a user question is still pending")
    if state.observations and state.observations[-1].result in {"error", "rejected"}:
        return FinishCheck(
            False, "the most recent orchestration error is unresolved"
        )
    unresolved = tuple(
        task.id for task in state.tasks
        if task.required and task.status in UNRESOLVED_TASK_STATUSES
    )
    if unresolved:
        return FinishCheck(False, "required tasks are unresolved", unresolved)
    if not state.artifacts:
        return FinishCheck(False, "the run has no result artifacts")
    return FinishCheck(True)


def task_fingerprint(executor: Producer, capability: str,
                     payload: dict[str, Any]) -> str:
    """Return a stable semantic identity for deduplicating planned work."""
    ignored = {"description", "supersedes_task_id", "_retry_scheduled"}
    normalized = {
        key: payload[key] for key in sorted(payload)
        if key not in ignored and payload[key] not in ("", [], None)
    }
    raw = json.dumps(
        {"executor": executor.value, "capability": capability,
         "input": normalized},
        ensure_ascii=True, sort_keys=True, default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def normalize_recommended_action(
    action: dict[str, Any], state: ResearchState,
) -> tuple[Producer, AgentKind, str, dict[str, Any]]:
    """Translate an ExpAgent recommendation into a ResAgent task contract."""
    action_type = str(action.get("type", "")).strip()
    plan = dict(action.get("plan") or {})
    plan_kind = str(plan.get("kind", "")).strip()
    semantic_kind = plan_kind or action_type
    if action_type and plan_kind and action_type != plan_kind:
        raise ValueError(
            f"action type {action_type!r} does not match plan.kind {plan_kind!r}"
        )

    explicit = str(action.get("executor") or plan.get("executor") or "").strip()
    if explicit:
        try:
            executor = Producer(explicit)
        except ValueError as exc:
            raise ValueError(f"unknown executor: {explicit}") from exc
    else:
        executor = _infer_executor(semantic_kind, plan, state)

    if executor == Producer.CodingAgent:
        kind, capability = AgentKind.coding_task, "modify_code"
    elif executor == Producer.ReproAgent:
        kind = AgentKind.repro_task
        capability = (
            "run_experiment" if semantic_kind == "run_task"
            else "reproduce_experiment"
        )
        _inherit_repro_context(plan, state)
    elif executor == Producer.ExpAgent:
        kind, capability = AgentKind.advise, _expagent_capability(semantic_kind)
    elif executor == Producer.ResAgent and semantic_kind == "ask_user":
        kind, capability = AgentKind.ask_user, "request_user_input"
    else:
        raise ValueError(
            f"unsupported executor/task combination: {executor.value}/{semantic_kind}"
        )
    return executor, kind, capability, plan


def _infer_executor(kind: str, plan: dict[str, Any],
                    state: ResearchState) -> Producer:
    if kind == "coding_task":
        return Producer.CodingAgent
    if kind == "repro_task":
        return Producer.ReproAgent
    if kind in {"result_analysis", "literature_search", "literature_reference"}:
        return Producer.ExpAgent
    if kind == "ask_user":
        return Producer.ResAgent
    if kind == "run_task":
        if (plan.get("repo_url") or plan.get("paper_url")
                or plan.get("workspace_path") or plan.get("repo_path")):
            return Producer.ReproAgent
        if any(task.agent == Producer.ReproAgent for task in state.tasks):
            return Producer.ReproAgent
        raise ValueError("run_task has no repository or prior reproduction context")
    raise ValueError(f"unsupported recommended action type: {kind or '<empty>'}")


def _inherit_repro_context(plan: dict[str, Any], state: ResearchState) -> None:
    previous = next(
        (task for task in reversed(state.tasks)
         if task.agent == Producer.ReproAgent),
        None,
    )
    if previous is not None:
        for key in (
            "paper_url", "repo_url", "workspace_path", "codingagent_path",
            "dataset_cache_dir",
        ):
            if not plan.get(key) and previous.input.get(key):
                plan[key] = previous.input[key]
    if not plan.get("experiment_goal"):
        plan["experiment_goal"] = (
            plan.get("command_goal") or plan.get("task_goal") or ""
        )
    if not plan.get("repo_url") and not (plan.get("workspace_path") or plan.get("repo_path")):
        raise ValueError(
            "reproduction task has no repo_url or source workspace"
        )


def _expagent_capability(kind: str) -> str:
    return {
        "result_analysis": "analyze_result",
        "literature_search": "search_literature",
        "literature_reference": "review_literature",
    }.get(kind, "scientific_advice")


def required_from_priority(priority: TaskPriority,
                           action: dict[str, Any]) -> bool:
    """Explicit requirement wins; high-priority work blocks finish by default."""
    if "required" in action:
        return bool(action["required"])
    return priority == TaskPriority.high
