"""Deterministic contracts for module routing and run lifecycle checks.

V2: executor routing is derived from the frozen scientific-capability
vocabulary (see capabilities.py), never from legacy action names or a
hard-coded team table. This module also owns the scientific-closure
invariant: completed experiments must be covered by a completed
`analyze_results` task before the run may finish (unless the run is an
explicit engineering smoke test with `analysis_required=False`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..models import (
    ActionName, AgentKind, AgentTask, DecisionRecord, Producer,
    ResearchState, RunStatus, TaskPriority, TaskStatus,
)
from ..capabilities import CapabilityError, V2_CAPABILITY_TO_PRODUCER


TERMINAL_RUN_STATUSES = {RunStatus.completed, RunStatus.failed}
UNRESOLVED_TASK_STATUSES = {
    TaskStatus.pending, TaskStatus.running, TaskStatus.failed,
    TaskStatus.blocked, TaskStatus.needs_user_input,
}

# capability -> (internal AgentKind, canonical capability string).
# AgentTask.agent/kind stay as ResAgent's internal execution model; only the
# `capability` field crosses the module boundary.
_CAPABILITY_KIND: dict[str, tuple[AgentKind, str]] = {
    "modify_code": (AgentKind.coding_task, "modify_code"),
    "reproduce_experiment": (AgentKind.repro_task, "reproduce_experiment"),
    "execute_experiment": (AgentKind.repro_task, "execute_experiment"),
    "analyze_results": (AgentKind.advise, "analyze_results"),
    "search_literature": (AgentKind.advise, "search_literature"),
    "ask_user": (AgentKind.ask_user, "ask_user"),
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


def resolve_action(
    action: dict[str, Any], registry=None,
) -> tuple[Producer, AgentKind, str]:
    """Resolve one V2 scientific action into a ResAgent task contract.

    The action is a discriminated union on `capability` (flat, no `type` or
    `plan.kind`). Executor resolution is deterministic: via the capability
    registry when one is available (fail-closed on missing/conflicting
    declarations), else via the frozen V2 vocabulary.
    """
    capability = str(action.get("capability", "")).strip()
    entry = _CAPABILITY_KIND.get(capability)
    if entry is None:
        raise CapabilityError(f"unknown scientific capability {capability!r}")

    if registry is not None:
        executor = registry.resolve(capability)
    else:
        executor = V2_CAPABILITY_TO_PRODUCER[capability]

    kind, canonical = entry
    return executor, kind, canonical


def experiment_tasks(state: ResearchState) -> list[AgentTask]:
    """Tasks that produce raw experiment results (ReproAgent operator)."""
    return [
        task for task in state.tasks
        if task.agent == Producer.ReproAgent
        and task.capability in {"execute_experiment", "reproduce_experiment"}
    ]


def analysis_coverage(state: ResearchState, experiment_task_id: str) -> str:
    """Return ``covered | missing | not_required`` for one experiment task.

    ``covered`` means a completed ExpAgent ``analyze_results`` task depends on
    the experiment and produced a scientific decision. ``not_required`` is
    returned when the run is an engineering smoke test or the experiment is
    not a completed result-producing task.
    """
    if not state.analysis_required:
        return "not_required"
    experiment = state.find_task(experiment_task_id)
    if experiment is None or experiment.status != TaskStatus.completed:
        return "not_required"
    for task in state.tasks:
        if (
            task.agent == Producer.ExpAgent
            and task.capability == "analyze_results"
            and task.status == TaskStatus.completed
            and experiment_task_id in task.depends_on
        ):
            return "covered"
    return "missing"


def _uncovered_required_experiments(state: ResearchState) -> list[str]:
    """Completed required experiments whose results are not yet analyzed."""
    return [
        task.id for task in experiment_tasks(state)
        if task.status == TaskStatus.completed
        and task.required
        and analysis_coverage(state, task.id) == "missing"
    ]


def ensure_analysis_coverage(
    state: ResearchState, experiment_task: AgentTask,
) -> AgentTask | None:
    """Create one deterministic ExpAgent ``analyze_results`` task if missing.

    The task fingerprint is derived from the experiment's artifact IDs so an
    equivalent fallback is created at most once. This is an orchestration
    invariant fix (second line of defense after the ExpAgent validator), not
    an LLM suggestion. Returns the new task, or None if coverage already
    exists or analysis is not required.
    """
    if not state.analysis_required:
        return None
    if experiment_task.capability not in {"execute_experiment", "reproduce_experiment"}:
        return None
    if analysis_coverage(state, experiment_task.id) != "missing":
        return None
    for task in state.tasks:
        if (
            task.agent == Producer.ExpAgent
            and task.capability == "analyze_results"
            and experiment_task.id in task.depends_on
        ):
            return None

    artifact_ids = sorted(experiment_task.artifacts)
    fingerprint = task_fingerprint(
        Producer.ExpAgent, "analyze_results",
        {"depends_on_artifacts": artifact_ids},
    )
    if state.find_task_by_fingerprint(fingerprint) is not None:
        return None

    task = AgentTask(
        id=f"task_{state.next_task_number():03d}",
        source=experiment_task.id,
        agent=Producer.ExpAgent,
        kind=AgentKind.advise,
        capability="analyze_results",
        required=True,
        fingerprint=fingerprint,
        action_id=f"analyze_{experiment_task.id}",
        project_ref=experiment_task.project_ref,
        depends_on=[experiment_task.id],
        input={
            "description": (
                "Orchestration invariant: analyze the completed experiment "
                "results and form a scientific conclusion."
            ),
            "task_goal": (
                "Analyze the completed experiment results and form a "
                "scientific conclusion."
            ),
        },
    )
    state.tasks.append(task)
    state.decisions.append(DecisionRecord(
        id=f"decision_{state.next_decision_number():03d}",
        made_by="ResAgent",
        reason=(
            "Orchestration invariant fix: completed experiment results must "
            "be scientifically analyzed before finish."
        ),
        selected_action=ActionName.call_exp_agent.value,
        evidence=[experiment_task.id, *artifact_ids],
    ))
    return task


def allowed_action_candidates(state: ResearchState) -> list[dict[str, Any]]:
    """Build exact actions the planner may choose in the current state.

    Only registered, runnable tasks are exposed as candidates (plus the
    built-in ask_user and finish). There is no free-floating "re-consult"
    hint: initial consultation is expressed by the initial ExpAgent advisory
    task, and result analysis by a task-bound analyze_results task.
    """
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
    uncovered = _uncovered_required_experiments(state)
    if uncovered:
        return FinishCheck(
            False,
            "experiment results are not yet scientifically analyzed",
            tuple(uncovered),
        )
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
