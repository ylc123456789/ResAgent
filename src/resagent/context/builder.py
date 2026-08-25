"""Build compact context for controllers and adapters from ResearchState."""

from __future__ import annotations

from pathlib import Path

from .policy import ContextPolicy
from ..models import ResearchState, AgentTask, Observation, DecisionRecord
from ..resources import resolve_artifact_path
from ..controller.contracts import allowed_action_candidates


# ── Controller context (for LLM planner) ──────────────────────────────────────

def build_controller_context(state: ResearchState, model: str | None = None) -> str:
    """Build a structured text summary of the research state for the LLM controller.

    This is the function that feeds the agentic loop. Keep it dense but
    complete enough that the LLM can make a good orchestration decision.
    """
    policy = ContextPolicy.for_model(model)
    budget_chars = policy.input_budget_tokens * 3  # ~3 chars/token rough estimate
    parts: list[str] = []

    # Goal (always kept)
    parts.append(f"## Research Goal\n{state.run.research_goal}")

    # Status (always kept)
    parts.append(f"\n## Run Status\n"
                 f"- Status: {state.run.status.value}\n"
                 f"- Tasks: {len(state.tasks)} total, "
                 f"{_count_by_status(state.tasks, 'pending')} pending, "
                 f"{_count_by_status(state.tasks, 'running')} running, "
                 f"{_count_by_status(state.tasks, 'completed')} completed, "
                 f"{_count_by_status(state.tasks, 'failed')} failed\n"
                 f"- Artifacts: {len(state.artifacts)}\n"
                 f"- Budget: {state.budget.tasks_run}/{state.budget.max_tasks} tasks, "
                 f"{state.budget.api_calls_used}/{state.budget.max_api_calls} api calls")

    # Current summary (always kept)
    if state.current_summary:
        parts.append(f"\n## Summary\n{state.current_summary}")

    if state.resources:
        parts.append("\n## Registered Resources\n" + "\n".join(
            f"- {resource.kind}:{resource.id} path={resource.path or '-'} "
            f"repo={resource.repo or '-'} certification={resource.certification or '-'}"
            + (
                f" state={resource.state or '-'} manager={resource.manager or '-'} "
                f"last_used={resource.last_used_at or '-'}"
                if resource.manifest_path else ""
            )
            for resource in state.resources
        ))

    # User directives (always kept, already capped at 3)
    if state.user_directives:
        lines = ["\n## User Directives (latest last)"]
        for d in state.user_directives[-3:]:
            lines.append(
                f"- [{d.ts:%Y-%m-%d %H:%M}] kind={d.kind.value} "
                f"handled={d.handled}: {d.text}"
            )
        parts.append("\n".join(lines))

    # Tasks
    tasks_text = _format_tasks(state.tasks)
    if tasks_text:
        parts.append(f"\n## Tasks\n{tasks_text}")

    # Recent artifacts (may be trimmed by budget)
    artifacts_text = _format_artifacts(state, policy)
    if artifacts_text:
        parts.append(f"\n## Recent Artifacts\n{artifacts_text}")

    # Recent decisions
    decisions_text = _format_decisions(state.decisions, max_items=5)
    if decisions_text:
        parts.append(f"\n## Recent Decisions\n{decisions_text}")

    # Recent observations
    obs_text = _format_observations(state.observations, policy)
    if obs_text:
        parts.append(f"\n## Recent Observations\n{obs_text}")

    candidates = allowed_action_candidates(state)
    parts.append("\n## Allowed Actions\n" +
                 "\n".join(f"- {item}" for item in candidates))

    # Budget enforcement: if total exceeds budget, trim lowest-priority sections
    parts = _enforce_budget(parts, budget_chars)
    return "\n".join(parts)


# ── Adapter context (for downstream module calls) ─────────────────────────────

def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value] if value.strip() else []
    return list(value)


def build_codingagent_context(task: AgentTask) -> dict:
    """Extract CodeTaskSpec-like dict from an AgentTask."""
    artifacts = task.input.get("input_artifacts", [])
    return {
        "workspace_path": task.input.get("workspace_path")
                       or task.input.get("repo_path", ""),
        "task_goal": task.input.get("task_goal", ""),
        "readonly_inputs": [
            {
                "id": item.get("artifact_id", ""),
                "path": item.get("path", ""),
                "description": _readonly_input_description(item),
            }
            for item in artifacts
            if isinstance(item, dict)
            and item.get("artifact_id") and item.get("path")
        ],
        "constraints": _as_list(task.input.get("constraints", [])),
        "verify_commands": _as_list(task.input.get("verify_commands", [])),
        "allowed_paths": _as_list(task.input.get("allowed_paths", [])),
        "output_dir": task.input.get("output_dir", ""),
        "repo_url": task.input.get("repo_url", ""),
        "branch": task.input.get("branch", ""),
        "env_policy": task.input.get("env_policy", "auto"),
        "env_name": task.input.get("env_name", ""),
        "requires_gpu": bool(task.input.get("requires_gpu", False)),
        "project_ref": task.project_ref,
    }


def build_reproagent_context(task: AgentTask) -> dict:
    """Extract ReproTask-like dict from an AgentTask."""
    artifacts = task.input.get("input_artifacts", [])
    experiment_goal = _goal_with_dependency_artifacts(
        task.input.get("experiment_goal", ""), artifacts,
    )
    return {
        "paper_url": task.input.get("paper_url", ""),
        "repo_url": task.input.get("repo_url", ""),
        "copy_from": task.input.get("copy_from", ""),
        "external_repo_path": task.input.get("external_repo_path", ""),
        "setup_only": bool(task.input.get("setup_only", False)),
        "allow_code_delegation": bool(task.input.get("allow_code_delegation", False)),
        "env_name": task.input.get("env_name", ""),
        "input_artifacts": [
            {
                "path": item.get("path", ""),
                "description": _artifact_description(item),
            }
            for item in artifacts
            if isinstance(item, dict) and item.get("path")
        ],
        "experiment_goal": experiment_goal,
        "workspace_dir": task.input.get("workspace_dir", ""),
        "api_base": task.input.get("api_base", ""),
        "api_key_env": task.input.get("api_key_env", ""),
        "model": task.input.get("model", ""),
        "timeout": task.input.get("timeout", 0),
        "mirror_profile": task.input.get("mirror_profile", ""),
        "codingagent_path": task.input.get("codingagent_path", ""),
        "dataset_cache_dir": task.input.get("dataset_cache_dir", ""),
        "requires_gpu": bool(task.input.get("requires_gpu", False)),
        "expected_metrics": _as_list(task.input.get("expected_metrics", [])),
        "expected_artifacts": _as_list(task.input.get("expected_artifacts", [])),
        "success_criteria": _as_list(task.input.get("success_criteria", [])),
        "project_ref": task.project_ref,
    }


def _readonly_input_description(item: dict) -> str:
    """Describe an input by identity without copying untrusted artifact text."""
    kind = str(item.get("type") or "artifact")
    producer = str(item.get("producer_task_id") or "").strip()
    return f"{kind} produced by {producer}" if producer else kind


def _artifact_description(item: dict) -> str:
    parts = [
        str(item.get("summary", "")).strip(),
        f"producer task {item.get('producer_task_id', '')}"
        if item.get("producer_task_id") else "",
        f"artifact {item.get('artifact_id', '')}"
        if item.get("artifact_id") else "",
    ]
    return "; ".join(part for part in parts if part)


def _goal_with_dependency_artifacts(goal: str, artifacts: list[dict]) -> str:
    """Append authoritative dependency outputs without guessing filenames."""
    if not artifacts:
        return goal
    lines = [
        goal.rstrip(),
        "",
        "## Dependency artifacts",
        "These are authoritative outputs from completed prerequisite tasks. "
        "Use their actual paths; do not infer output directories from the plan.",
    ]
    for item in artifacts:
        lines.append(
            f"- task={item.get('producer_task_id', '')} "
            f"artifact={item.get('artifact_id', '')} "
            f"type={item.get('type', '')} path={item.get('path', '')}"
        )
    return "\n".join(lines)


# ── helpers ───────────────────────────────────────────────────────────────────

def _count_by_status(tasks: list[AgentTask], status: str) -> int:
    return sum(1 for t in tasks if t.status.value == status)


def _format_tasks(tasks: list[AgentTask]) -> str:
    lines = []
    for t in tasks:
        marker = _status_marker(t.status.value)
        err = f" ({t.error[:80]})" if t.error else ""
        attempts = f" attempts={len(t.attempts)}" if t.attempts else ""
        lines.append(f"- {marker} [{t.id}] {t.agent.value}/{t.kind.value} "
                     f"cap={t.capability or '-'} pri={t.priority.value} "
                     f"required={t.required} project={t.project_ref or '-'} "
                     f"depends_on={t.depends_on or []}{attempts}{err}")
        inp = t.input
        if inp.get("paper_url"):
            lines.append(f"    paper_url: {inp['paper_url'][:100]}")
        if inp.get("repo_url"):
            lines.append(f"    repo_url: {inp['repo_url'][:100]}")
        if inp.get("experiment_goal"):
            lines.append(f"    experiment_goal: {inp['experiment_goal'][:120]}")
        if inp.get("workspace_path"):
            lines.append(f"    workspace_path: {inp['workspace_path'][:100]}")
        if inp.get("task_goal"):
            lines.append(f"    task_goal: {inp['task_goal'][:120]}")
        if inp.get("input_artifacts"):
            lines.append(
                f"    input_artifacts: {len(inp['input_artifacts'])} dependency output(s)"
            )
    return "\n".join(lines)


def _format_artifacts(state: ResearchState, policy: ContextPolicy) -> str:
    lines = []
    for artifact in state.artifacts[-policy.artifact_count:]:
        lines.append(f"- [{artifact.id}] {artifact.producer.value} {artifact.type.value}: {_clip_middle(artifact.summary, policy.artifact_summary_chars)}")
    evidence = _latest_result_evidence(state, policy.latest_result_chars)
    if evidence:
        lines.append("\n### Latest Result Evidence\n" + evidence)
    return "\n".join(lines)


def _latest_result_evidence(state: ResearchState, limit: int) -> str:
    for artifact in reversed(state.artifacts):
        if artifact.type.value != "repro_result":
            continue
        path = resolve_artifact_path(state, artifact.path)
        root = (Path(state.run.workspace_dir) / state.run.run_id).resolve()
        if root not in path.parents or not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".json"}:
            continue
        try:
            return f"[{artifact.id} @ {artifact.path}]\n" + _clip_middle(path.read_text(encoding="utf-8", errors="replace"), limit)
        except OSError:
            continue
    return ""


def _enforce_budget(parts: list[str], budget_chars: int) -> list[str]:
    """Trim lower-priority sections if total exceeds the input budget.

    Only Observations, Decisions, Artifacts, and Tasks are trimmable (trimmed in
    that order, lowest priority first). Goal, Status, Summary, and User Directives
    are always kept.
    """
    total = sum(len(p) for p in parts)
    if total <= budget_chars:
        return parts

    # Section markers ordered from lowest to highest priority for trimming
    trim_order = [
        "## Recent Observations",
        "## Recent Decisions",
        "## Recent Artifacts",
        "## Tasks",
    ]

    for marker in trim_order:
        if total <= budget_chars:
            break
        for i, p in enumerate(parts):
            if p.startswith(marker):
                total -= len(p)
                parts[i] = p[:budget_chars // 4] + "\n... [section trimmed to fit budget]"
                total += len(parts[i])
                break

    return parts


def _clip_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    return text[:head] + "\n... [truncated] ...\n" + text[-(limit - head):]

def _format_decisions(decisions: list[DecisionRecord], max_items: int) -> str:
    lines = []
    for d in decisions[-max_items:]:
        lines.append(f"- [{d.id}] {d.made_by}: {d.reason[:120]}")
    return "\n".join(lines)


def _format_observations(observations: list[Observation], policy: ContextPolicy) -> str:
    lines = []
    for o in observations[-policy.observation_count:]:
        lines.append(f"- [{o.action.value if o.action else '?'}] {o.result}: {_clip_middle(o.detail, policy.observation_chars)}")
    return "\n".join(lines)


def _status_marker(status: str) -> str:
    return {
        "pending": "⬜",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "blocked": "🚫",
        "skipped": "⏭️",
        "needs_user_input": "🙋",
    }.get(status, "❓")
