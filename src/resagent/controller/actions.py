"""Controller action dispatch, task execution, and retry handling."""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import (
    ResearchState, Attempt, PendingQuestion, Observation, ActionName,
    Producer, TaskStatus,
)
from .planner import PlannedAction
from ..policies.retry import RetryPolicy, classify_transient
from .contracts import (
    dependencies_satisfied, ensure_analysis_coverage, validate_finish,
)
from ..persistence.workspace import WorkspaceLayout
from ..resources import (
    acquire_lease,
    materialize_dependency_artifacts,
    materialize_task_bindings,
    register_task_resources,
    release_lease,
    resume_repaired_tasks,
    schedule_coding_repair,
)


def _latest_optional_recommendations(state: ResearchState) -> list[dict]:
    """Return optional actions from the newest scientific decision."""
    for artifact in reversed(state.artifacts):
        raw = artifact.metadata.get("raw_decision")
        if not isinstance(raw, dict):
            continue
        return [
            action for action in raw.get("recommended_actions", [])
            if isinstance(action, dict) and not bool(action.get("required", True))
        ]
    return []


class ControllerActions:
    """Action handlers shared by the public Controller loop."""

    def _acquire_env_lease(self, state: ResearchState, task) -> str:
        """Register a RESOURCE_LEASE_V1 when a manifest env was injected."""
        root = getattr(self.resources, "root", "") if self.resources else ""
        env_id = str(task.input.get("_lease_env_id", ""))
        lease_path = acquire_lease(root, env_id, state.run.run_id, task.id)
        if env_id and not lease_path:
            raise RuntimeError(
                f"resource temporarily unavailable: environment {env_id}"
            )
        return lease_path

    def _execute(self, state: ResearchState, planned: PlannedAction) -> Observation:
        layout = WorkspaceLayout(state.run.workspace_dir, state.run.run_id)

        handlers = {
            ActionName.call_exp_agent: self._handle_exp_agent,
            ActionName.call_coding_agent: self._handle_coding_agent,
            ActionName.call_repro_agent: self._handle_repro_agent,
            ActionName.classify_failure: self._handle_classify_failure,
            ActionName.ask_user: self._handle_ask_user,
            ActionName.finish: self._handle_finish,
        }

        handler = handlers.get(planned.action, self._handle_unknown)
        return handler(state, planned, layout)

    def _handle_exp_agent(self, state, planned, layout) -> Observation:
        task = None
        task_id = planned.params.get("task_id", "")
        pending = [
            t.id for t in state.tasks
            if t.agent == Producer.ExpAgent and t.status == TaskStatus.pending
        ]
        if not task_id and pending:
            return Observation(
                action=ActionName.call_exp_agent,
                result="error",
                detail=f"ExpAgent task_id is required. Pending: {pending}",
                task_ids=pending,
            )
        if task_id:
            task = self._require_task(state, planned, Producer.ExpAgent)
            if isinstance(task, Observation):
                return task
            task.status = TaskStatus.running
            task.attempts.append(Attempt(
                attempt_number=len(task.attempts) + 1,
                started_at=datetime.now(timezone.utc),
            ))
            materialize_dependency_artifacts(state, task)

        result = self.expagent.advise(state, layout, task=task)
        artifact = result["artifact"]
        state.register_artifact(artifact, task)
        for spawned in result.get("tasks", []):
            supersedes = spawned.input.get("supersedes_task_id", "")
            previous = state.find_task(supersedes) if supersedes else None
            if previous is not None and previous.status in (TaskStatus.pending, TaskStatus.failed, TaskStatus.blocked):
                previous.status = TaskStatus.skipped
                previous.error = f"Superseded by {spawned.id}."
            state.tasks.append(spawned)

        issues = result["raw"].get("_normalization_issues", [])
        task_ids = [t.id for t in result.get("tasks", [])]
        if task is not None:
            task.status = TaskStatus.completed
            task.attempts[-1].artifacts.append(artifact.id)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task_ids.insert(0, task.id)
            state.budget.tasks_run += 1

        return Observation(
            action=ActionName.call_exp_agent,
            result="error" if issues else "ok",
            detail=(
                "Task contract validation failed: " + "; ".join(issues)
                if issues else planned.analysis or result["raw"].get("summary", "")
            ),
            artifact_ids=[artifact.id],
            task_ids=task_ids,
        )

    def _handle_coding_agent(self, state, planned, layout) -> Observation:
        task = self._require_task(state, planned, Producer.CodingAgent)
        if isinstance(task, Observation):
            return task  # error observation from _require_task
        materialize_task_bindings(state, task, layout, self.shared_workspace,
                                  resources=self.resources)

        task.status = TaskStatus.running
        attempt_num = len(task.attempts) + 1
        task.attempts.append(Attempt(attempt_number=attempt_num,
                                    started_at=datetime.now(timezone.utc)))
        lease_path = ""

        try:
            lease_path = self._acquire_env_lease(state, task)
            result = self.codingagent.execute(task, layout, attempt_num)
            state.register_artifact(result["artifact"], task)
            outcome = result.get("outcome", "completed")
            if outcome in {"completed", "completed_with_warnings"}:
                task.status = TaskStatus.completed
                task.error = ""
            elif outcome == "blocked":
                task.status = TaskStatus.blocked
            elif outcome == "needs_user_input":
                task.status = TaskStatus.needs_user_input
            else:
                task.status = TaskStatus.failed
                self._schedule_retry(state, task, str(result["raw"].get("summary", "CodingAgent failed")))
            if outcome == "completed_with_warnings":
                task.warnings.append(
                    str(result["raw"].get("summary", "Completed with warnings"))[:2000]
                )
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].artifacts.append(result["artifact"].id)
            state.budget.tasks_run += 1
            register_task_resources(
                state, task,
                result.get("session_manifest", ""),
                result.get("workspace_path", ""),
            )
            if task.status == TaskStatus.completed:
                resume_repaired_tasks(state, task)

            return Observation(
                action=ActionName.call_coding_agent,
                result="ok" if task.status == TaskStatus.completed else ("user_response_required" if task.status == TaskStatus.needs_user_input else "error"),
                detail=result["raw"].get("summary", f"Coding task {task.id} {task.status.value}."),
                artifact_ids=[result["artifact"].id],
                task_ids=[task.id],
            )
        except Exception as e:
            task.status = TaskStatus.failed
            task.error = str(e)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].error = str(e)
            self._schedule_retry(state, task, str(e))
            return Observation(
                action=ActionName.call_coding_agent,
                result="error",
                detail=str(e),
                task_ids=[task.id],
            )
        finally:
            release_lease(lease_path)

    def _handle_repro_agent(self, state, planned, layout) -> Observation:
        task = self._require_task(state, planned, Producer.ReproAgent)
        if isinstance(task, Observation):
            return task
        materialize_task_bindings(state, task, layout, self.shared_workspace,
                                  resources=self.resources)

        task.status = TaskStatus.running
        attempt_num = len(task.attempts) + 1
        task.attempts.append(Attempt(attempt_number=attempt_num,
                                    started_at=datetime.now(timezone.utc)))
        lease_path = ""

        try:
            lease_path = self._acquire_env_lease(state, task)
            result = self.reproagent.execute(task, layout, attempt_num)
            state.register_artifact(result["artifact"], task)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].artifacts.append(result["artifact"].id)
            state.budget.tasks_run += 1

            outcome = result.get("outcome", result.get("returncode") == 0 and "completed" or "failed")
            if outcome == "completed":
                task.status = TaskStatus.completed
                task.error = ""
                obs_result = "ok"
            elif outcome == "completed_with_warnings":
                task.status = TaskStatus.completed
                task.error = ""
                task.warnings.append(
                    str(result["raw"].get("summary", "Completed with warnings"))[:2000]
                )
                obs_result = "ok"
            elif outcome == "blocked":
                task.status = TaskStatus.blocked
                obs_result = "error"
            elif outcome == "needs_user_input":
                task.status = TaskStatus.needs_user_input
                obs_result = "user_response_required"
            else:
                task.status = TaskStatus.failed
                self._schedule_retry(state, task, str(result["raw"].get("summary", "ReproAgent failed")))
                obs_result = "error"

            materialized_workspace = result.get("workspace_path", "")
            if task.status == TaskStatus.completed and materialized_workspace:
                task.input["workspace_path"] = materialized_workspace
            register_task_resources(
                state, task,
                result.get("session_manifest", ""),
                materialized_workspace,
            )
            if task.status == TaskStatus.completed:
                ensure_analysis_coverage(state, task)
            spawned = None
            detail = result["raw"].get("summary", "")
            if task.status == TaskStatus.blocked:
                spawned = schedule_coding_repair(
                    state, task, result.get("coding_issues", []),
                    materialized_workspace,
                )
                if spawned is not None:
                    # The ReproAgent action was handled successfully: its
                    # structured blocker has become a runnable CodingAgent
                    # task. Keep the operator blocked until that repair
                    # completes, but do not report an orchestration failure.
                    obs_result = "ok"
                    detail = (
                        f"ReproAgent requested code changes; scheduled "
                        f"CodingAgent repair {spawned.id}.\n{detail}"
                    )

            return Observation(
                action=ActionName.call_repro_agent,
                result=obs_result,
                detail=detail,
                artifact_ids=[result["artifact"].id],
                task_ids=[task.id] + ([spawned.id] if spawned else []),
            )
        except Exception as e:
            task.status = TaskStatus.failed
            task.error = str(e)
            task.attempts[-1].finished_at = datetime.now(timezone.utc)
            task.attempts[-1].error = str(e)
            self._schedule_retry(state, task, str(e))
            return Observation(
                action=ActionName.call_repro_agent,
                result="error",
                detail=str(e),
                task_ids=[task.id],
            )
        finally:
            release_lease(lease_path)

    def _next_retry_action(self, state: ResearchState) -> PlannedAction | None:
        """Retry transient failures before asking the planner for a new task."""
        for task in state.tasks:
            if not task.input.get("_retry_scheduled"):
                continue
            action = {
                Producer.CodingAgent: ActionName.call_coding_agent,
                Producer.ReproAgent: ActionName.call_repro_agent,
            }.get(task.agent)
            if action is None or task.status != TaskStatus.pending:
                continue
            task.input.pop("_retry_scheduled", None)
            return PlannedAction(
                action=action,
                params={"task_id": task.id, **task.input},
                reason=f"Retrying transient failure for {task.id}.",
                analysis="Retry is prioritized over new planning.",
            )
        return None

    def _schedule_retry(self, state, task, error: str) -> bool:
        """Schedule a bounded transient retry for the next controller step."""
        task.error = error
        task.attempts[-1].error = error
        if RetryPolicy(state.budget.max_task_retries).should_retry(task, error):
            task.status = TaskStatus.pending
            task.input["_retry_scheduled"] = True
            return True
        return False

    def _require_task(self, state, planned, expected_agent):
        """Validate task_id and return the task, or an error Observation."""
        task_id = planned.params.get("task_id", "")
        if not task_id:
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Missing task_id. {expected_agent.value} calls require a task_id from the pending task list.",
            )
        task = state.find_task(task_id)
        if task is None:
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Task {task_id} not found. Available: {[t.id for t in state.tasks]}",
            )
        if task.agent != expected_agent:
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Task {task_id} belongs to {task.agent.value}, not {expected_agent.value}.",
            )
        if not dependencies_satisfied(task, state):
            waiting = [
                dependency for dependency in task.depends_on
                if state.find_task(dependency) is None
                or state.find_task(dependency).status not in {
                    TaskStatus.completed, TaskStatus.skipped,
                }
            ]
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Task {task_id} is waiting for dependencies: {waiting}.",
            )
        if task.status not in (TaskStatus.pending, TaskStatus.failed, TaskStatus.blocked):
            return Observation(
                action=planned.action,
                result="error",
                detail=f"Task {task_id} is {task.status.value}, not pending/failed/blocked.",
            )
        return task

    def _handle_classify_failure(self, state, planned, layout) -> Observation:
        task_id = planned.params.get("task_id", "")
        error = planned.params.get("error_message", "")

        # Deterministic classifier first - avoids LLM calls for known network errors.
        classification = classify_transient(error)
        if classification.get("category") == "unknown":
            classification = self.planner.classify_failure(task_id, error)

        detail = (
            f"Failure classified as: {classification.get('category', 'unknown')}. "
            f"Recommended: {classification.get('recommended_action', 'investigate')}."
        )

        return Observation(
            action=ActionName.classify_failure,
            result="ok",
            detail=detail,
            task_ids=[task_id] if task_id else [],
        )

    def _handle_ask_user(self, state, planned, layout) -> Observation:
        question_text = planned.params.get("question", "Continue?")

        task_id = planned.params.get("task_id", "")
        if task_id:
            task = self._require_task(state, planned, Producer.ResAgent)
            if isinstance(task, Observation):
                return task
            task.status = TaskStatus.needs_user_input
            question_text = task.input.get("question") or question_text

        # Check if the same question is already pending - no duplicates.
        if state.pending_question is not None:
            return Observation(
                action=ActionName.ask_user,
                result="user_response_required",
                detail=f"Question already pending: {state.pending_question.text[:200]}",
            )

        # Create persisted question and pause
        import uuid
        state.pending_question = PendingQuestion(
            question_id=f"q_{uuid.uuid4().hex[:8]}",
            text=question_text,
            task_id=task_id or None,
            requested_fields=planned.params.get("requested_fields", []),
        )

        return Observation(
            action=ActionName.ask_user,
            result="user_response_required",
            detail=f"Question: {question_text[:200]}",
        )

    def _handle_finish(self, state, planned, layout) -> Observation:
        check = validate_finish(state)
        if not check.allowed:
            return Observation(
                action=ActionName.finish,
                result="rejected",
                detail=f"Cannot finish: {check.reason}.",
                task_ids=list(check.task_ids),
            )
        summary = str(planned.params.get("summary") or "").strip() or "Run finished."
        # Optional recommendations stay in the scientific decision rather than
        # entering the committed task queue.
        followups = _latest_optional_recommendations(state)
        if followups:
            lines = "\n".join(
                f"- {item.get('action_id', '(unnamed)')} "
                f"({item.get('capability', 'unknown')}): "
                f"{str(item.get('objective', '')).strip() or '(no objective)'}"
                for item in followups
            )
            summary += f"\n\nProposed follow-ups (optional, not executed):\n{lines}"
        return Observation(
            action=ActionName.finish,
            result="ok",
            detail=summary,
        )

    def _handle_unknown(self, state, planned, layout) -> Observation:
        return Observation(
            action=planned.action,
            result="error",
            detail=f"Unknown action: {planned.action}",
        )
