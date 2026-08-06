"""Build compact context for controllers and adapters from ResearchState."""

from __future__ import annotations

from .models import ResearchState, AgentTask, Artifact, Observation, DecisionRecord


# ── Controller context (for LLM planner) ──────────────────────────────────────

def build_controller_context(state: ResearchState) -> str:
    """Build a structured text summary of the research state for the LLM controller.

    This is the function that feeds the agentic loop. Keep it dense but
    complete enough that the LLM can make a good orchestration decision.
    """
    parts: list[str] = []

    # Goal
    parts.append(f"## Research Goal\n{state.run.research_goal}")

    # Status
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

    # Current summary
    if state.current_summary:
        parts.append(f"\n## Summary\n{state.current_summary}")

    # Tasks (focus on active ones)
    tasks_text = _format_tasks(state.tasks)
    if tasks_text:
        parts.append(f"\n## Tasks\n{tasks_text}")

    # Recent artifacts
    artifacts_text = _format_artifacts(state.artifacts, max_items=10)
    if artifacts_text:
        parts.append(f"\n## Recent Artifacts\n{artifacts_text}")

    # Recent decisions
    decisions_text = _format_decisions(state.decisions, max_items=5)
    if decisions_text:
        parts.append(f"\n## Recent Decisions\n{decisions_text}")

    # Recent observations
    obs_text = _format_observations(state.observations, max_items=10)
    if obs_text:
        parts.append(f"\n## Recent Observations\n{obs_text}")

    return "\n".join(parts)


# ── Adapter context (for downstream module calls) ─────────────────────────────

def build_expagent_context(state: ResearchState) -> dict:
    """Build AdvisorContext-like dict for ExpAgent from current state."""
    return {
        "situation": state.current_summary or state.run.research_goal,
        "artifacts": [
            {"id": a.id, "type": a.type.value, "summary": a.summary}
            for a in state.artifacts[-20:]  # last 20 only
        ],
        "existing_plan": [
            {"id": t.id, "kind": t.kind.value, "status": t.status.value, "priority": t.priority.value}
            for t in state.tasks
        ],
    }


def build_codingagent_context(task: AgentTask) -> dict:
    """Extract CodeTaskSpec-like dict from an AgentTask."""
    def _as_list(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [v] if v.strip() else []
        return list(v)

    return {
        "repo_path": task.input.get("repo_path", ""),
        "task_goal": task.input.get("task_goal", ""),
        "constraints": _as_list(task.input.get("constraints", [])),
        "verify_commands": _as_list(task.input.get("verify_commands", [])),
        "allowed_paths": _as_list(task.input.get("allowed_paths", [])),
        "output_dir": task.input.get("output_dir", ""),
    }


def build_reproagent_context(task: AgentTask) -> dict:
    """Extract ReproTask-like dict from an AgentTask."""
    return {
        "paper_url": task.input.get("paper_url", ""),
        "repo_url": task.input.get("repo_url", ""),
        "experiment_goal": task.input.get("experiment_goal", ""),
        "workspace_dir": task.input.get("workspace_dir", ""),
        "api_base": task.input.get("api_base", ""),
        "api_key_env": task.input.get("api_key_env", ""),
        "model": task.input.get("model", ""),
        "timeout": task.input.get("timeout", 0),
        "mirror_profile": task.input.get("mirror_profile", ""),
        "codingagent_path": task.input.get("codingagent_path", ""),
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _count_by_status(tasks: list[AgentTask], status: str) -> int:
    return sum(1 for t in tasks if t.status.value == status)


def _format_tasks(tasks: list[AgentTask]) -> str:
    lines = []
    for t in tasks:
        marker = _status_marker(t.status.value)
        err = f" ({t.error[:80]})" if t.error else ""
        lines.append(f"- {marker} [{t.id}] {t.agent.value}/{t.kind.value} "
                     f"pri={t.priority.value}{err}")
    return "\n".join(lines)


def _format_artifacts(artifacts: list[Artifact], max_items: int) -> str:
    lines = []
    for a in artifacts[-max_items:]:
        lines.append(f"- [{a.id}] {a.producer.value} {a.type.value}: {a.summary[:120]}")
    return "\n".join(lines)


def _format_decisions(decisions: list[DecisionRecord], max_items: int) -> str:
    lines = []
    for d in decisions[-max_items:]:
        lines.append(f"- [{d.id}] {d.made_by}: {d.reason[:120]}")
    return "\n".join(lines)


def _format_observations(observations: list[Observation], max_items: int) -> str:
    lines = []
    for o in observations[-max_items:]:
        lines.append(f"- [{o.action.value if o.action else '?'}] {o.result}: {o.detail[:120]}")
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
