"""Deterministic contracts for module routing and run lifecycle checks.

V2: executor routing is derived from the frozen scientific-capability
vocabulary (see capabilities.py), never from legacy action names or a
hard-coded team table. This module also owns the scientific-closure
invariant: completed experiments must be covered by a completed
`analyze_results` task before the run may finish (unless the run is an
explicit engineering smoke test with `analysis_required=False`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import (
    ActionName, AgentKind, AgentTask, DecisionRecord, DirectiveKind, Producer,
    ResearchState, RunStatus, TaskPriority, TaskStatus,
)
from ..capabilities import CapabilityError, V2_CAPABILITIES
from .tasks import create_task, task_fingerprint


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

# The capability vocabulary is frozen in capabilities.V2_CAPABILITIES. This
# mapping only adds the internal AgentKind/canonical routing for each frozen
# capability, so it must never drift from the single source of truth.
assert set(_CAPABILITY_KIND) == set(V2_CAPABILITIES), (
    "capability vocabulary drift: _CAPABILITY_KIND != V2_CAPABILITIES"
)


FINAL_ACCEPTANCE_DECISION = "final_acceptance_decision"


@dataclass(frozen=True)
class FinishCheck:
    allowed: bool
    reason: str = ""
    task_ids: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


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
    action: dict[str, Any], registry,
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

    if registry is None:
        raise CapabilityError("capability registry is required for V2 routing")
    executor = registry.resolve(capability)

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
    experiment = state.find_task(experiment_task_id)
    if experiment is None or experiment.status != TaskStatus.completed:
        return "not_required"
    if not experiment_requires_analysis(state, experiment):
        return "not_required"
    decision_artifacts = {
        artifact.id for artifact in state.artifacts
        if artifact.type.value == "scientific_decision"
        and artifact.producer == Producer.ExpAgent
    }
    for task in state.tasks:
        if (
            task.agent == Producer.ExpAgent
            and task.capability == "analyze_results"
            and task.status == TaskStatus.completed
            and experiment_task_id in task.depends_on
            and any(item in decision_artifacts for item in task.artifacts)
        ):
            return "covered"
    return "missing"


def experiment_requires_analysis(
    state: ResearchState, experiment_task: AgentTask,
) -> bool:
    """Return the immutable analysis policy captured for one experiment."""
    if experiment_task.analysis_required is not None:
        return experiment_task.analysis_required
    return state.analysis_required


def _uncovered_completed_experiments(state: ResearchState) -> list[str]:
    """Completed experiments whose captured policy requires analysis."""
    return [
        task.id for task in experiment_tasks(state)
        if task.status == TaskStatus.completed
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
    if experiment_task.capability not in {"execute_experiment", "reproduce_experiment"}:
        return None
    if not experiment_requires_analysis(state, experiment_task):
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
        {
            "experiment_task_id": experiment_task.id,
            "depends_on_artifacts": artifact_ids,
        },
    )
    if state.find_task_by_fingerprint(fingerprint) is not None:
        return None

    task = create_task(
        state,
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


def ensure_directive_replan(state: ResearchState) -> AgentTask | None:
    """Create one ExpAgent re-plan task for unhandled user directives.

    A plan-revision directive (e.g. "改成单 seed") must actually change the
    plan. The controller has no direct "modify task" action, so it hands the
    directive to ExpAgent as a fresh advisory task. Information, confirmation,
    and control directives are deliberately excluded.
    """
    unhandled = [
        d for d in state.user_directives
        if not d.handled and d.kind == DirectiveKind.plan_revision
    ]
    if not unhandled:
        return None

    # Mark handled BEFORE creating the task, so a re-plan that itself pauses or
    # fails does not re-trigger an infinite re-plan loop on the same directive.
    for directive in unhandled:
        directive.handled = True
    directive_block = "\n".join(f"- {d.text}" for d in unhandled)

    task = create_task(
        state,
        source="user_directive",
        agent=Producer.ExpAgent,
        kind=AgentKind.advise,
        priority=TaskPriority.high,
        capability="",
        required=True,
        action_id="replan_from_directive",
        input={
            "description": (
                "The user issued a directive that may change the plan. Revise "
                "the scientific action graph accordingly."
            ),
            "task_goal": (
                "The user issued this directive:\n"
                f"{directive_block}\n\n"
                "Revise the plan to comply: add, remove, or supersede tasks as "
                "needed. If the current plan already complies, state that no "
                "change is required."
            ),
        },
    )
    state.decisions.append(DecisionRecord(
        id=f"decision_{state.next_decision_number():03d}",
        made_by="ResAgent",
        reason="Orchestration invariant fix: a new user directive requires re-planning.",
        selected_action=ActionName.call_exp_agent.value,
        evidence=[d.text for d in unhandled],
    ))
    return task


def apply_finish_control(state: ResearchState):
    """Apply one pending user finish request without scientific re-planning.

    Unstarted work is skipped, except required result analysis. The directive
    remains unhandled until validate_finish succeeds, so the controller will
    deterministically finish after any necessary analysis.
    """
    directive = next(
        (
            item for item in state.user_directives
            if not item.handled
            and item.kind == DirectiveKind.control
            and item.command == "finish"
        ),
        None,
    )
    if directive is None:
        return None

    for task in state.tasks:
        if task.status not in {
            TaskStatus.pending, TaskStatus.failed, TaskStatus.blocked,
        }:
            continue
        if task.required and task.capability == "analyze_results":
            continue
        task.status = TaskStatus.skipped
        task.error = "Skipped by explicit user finish request."
        task.input["superseded_by"] = "user_control:finish"
    return directive


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
        if task_attempt_limit_reached(state, task):
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


def task_attempt_limit_reached(state: ResearchState, task: AgentTask) -> bool:
    """Return whether an operator task has exhausted its execution attempts."""
    return (
        task.agent in {Producer.CodingAgent, Producer.ReproAgent}
        and task.status in {TaskStatus.failed, TaskStatus.blocked}
        and len(task.attempts) >= state.budget.max_task_retries
    )


def final_acceptance_issues(state: ResearchState) -> tuple[str, ...]:
    """Return unresolved issues explicitly reported by completed work.

    Only current task artifacts are considered, so a successful retry replaces
    stale warnings from an earlier attempt. Scientific questions come from the
    newest advisor decision; historical questions remain audit records only.
    """
    artifacts = {artifact.id: artifact for artifact in state.artifacts}
    issues: list[str] = []
    reported_paths = _reported_artifact_paths(state)

    for task in state.tasks:
        if not task.required or task.status != TaskStatus.completed:
            continue
        for expected in _issue_values(task.input.get("expected_artifacts")):
            if not _artifact_path_reported(expected, reported_paths):
                _append_issue(issues, f"Missing required artifact: {expected}")
        if not task.artifacts:
            continue
        artifact = artifacts.get(task.artifacts[-1])
        if artifact is None:
            continue
        metadata = artifact.metadata
        raw = metadata.get("raw_result", {})
        if not isinstance(raw, dict):
            raw = {}
        structured = raw.get("structured_result", {})
        delivery = (
            structured.get("delivery", {}) if isinstance(structured, dict) else {}
        )
        delivery_issues = (
            delivery.get("issues") if isinstance(delivery, dict) else None
        )
        for issue in _issue_values(delivery_issues):
            _append_issue(issues, issue)
        for issue in _issue_values(metadata.get("acceptance_issues")):
            _append_issue(issues, issue)

        outcome = metadata.get("outcome") or raw.get("outcome") or raw.get("status")
        if outcome == "completed_with_warnings" and not (
            isinstance(delivery, dict) and delivery.get("issues")
        ):
            _append_issue(issues, raw.get("summary") or artifact.summary)

    for artifact in reversed(state.artifacts):
        raw = artifact.metadata.get("raw_decision")
        if not isinstance(raw, dict):
            continue
        for issue in _issue_values(raw.get("needs_user_input")):
            _append_issue(issues, issue)
        break

    return tuple(issues)

def _reported_artifact_paths(state: ResearchState) -> set[str]:
    """Collect executor-reported paths from the canonical artifact registry."""
    current_artifact_ids = {
        task.artifacts[-1]
        for task in state.tasks
        if task.status == TaskStatus.completed and task.artifacts
    }
    paths: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip().replace("\\", "/")
        if text:
            paths.add(text.removeprefix("./"))

    for artifact in state.artifacts:
        if artifact.id not in current_artifact_ids:
            continue
        add(artifact.path)
        metadata = artifact.metadata
        for value in _issue_values(metadata.get("output_artifacts")):
            add(value)
        raw = metadata.get("raw_result", {})
        if not isinstance(raw, dict):
            continue
        for key in ("changed_files", "produced_files"):
            for value in _issue_values(raw.get(key)):
                add(value)
        structured = raw.get("structured_result", {})
        if not isinstance(structured, dict):
            continue
        for key in ("evidence_files", "evidence"):
            for value in _issue_values(structured.get(key)):
                if isinstance(value, dict):
                    add(value.get("path"))
                    add(value.get("source"))
                else:
                    add(value)
    return paths


def _artifact_path_reported(expected: Any, paths: set[str]) -> bool:
    target = str(expected or "").strip().replace("\\", "/").removeprefix("./")
    if not target:
        return True
    return any(path == target or path.endswith(f"/{target}") for path in paths)



def _issue_values(value: Any) -> list[Any]:
    if value is True:
        return ["Scientific advisor requires user input before completion."]
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _append_issue(issues: list[str], value: Any) -> None:
    if isinstance(value, dict):
        value = value.get("message") or value.get("issue") or str(value)
    text = str(value or "").strip()
    if text and text not in issues:
        issues.append(text)


def validate_finish(
    state: ResearchState, *, allow_final_issues: bool = False,
) -> FinishCheck:
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
    uncovered = _uncovered_completed_experiments(state)
    if uncovered:
        return FinishCheck(
            False,
            "experiment results are not yet scientifically analyzed",
            tuple(uncovered),
        )
    if not state.artifacts:
        return FinishCheck(False, "the run has no result artifacts")
    issues = final_acceptance_issues(state)
    if issues and not allow_final_issues:
        return FinishCheck(
            False,
            "final acceptance found unresolved issues",
            issues=issues,
        )
    return FinishCheck(True)
