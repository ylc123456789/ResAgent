"""High-level orchestration helpers — init, resume, run lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import ResearchState
from .persistence.state import init_state, save_state, load_state
from .config import Config, load_config
from .integrations.module_paths import resolve_all
from .planner import Planner
from .controller import Controller
from .adapters.expagent import ExpAgentAdapter
from .adapters.codingagent import CodingAgentAdapter
from .adapters.reproagent import ReproAgentAdapter
from .persistence.report import generate_all


def _generate_run_id() -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6]
    return f"res-{today}-{suffix}"


def init_run(
    goal: str,
    workspace_root: str = "runs",
    config: Config | None = None,
) -> ResearchState:
    """Create a new research run workspace and initial state."""
    cfg = config or load_config()
    run_id = _generate_run_id()
    ws = str(Path(workspace_root).resolve())
    state = init_state(run_id=run_id, workspace_dir=ws, research_goal=goal)
    save_state(state)
    return state


def build_controller(config: Config, mock: bool = False) -> Controller:
    """Build a Controller from config, resolving all module paths.

    Uses the 5-tier path resolution: CLI > env > config > import > vendor.
    Paths from config.agents are those already resolved through CLI/env/config.
    """
    modules = resolve_all(
        cli_expagent=config.cmd_expagent,
        cli_codingagent=config.cmd_codingagent,
        cli_reproagent=config.cmd_reproagent,
        config_expagent=config.agents.expagent,
        config_codingagent=config.agents.codingagent,
        config_reproagent=config.agents.reproagent,
    )

    if not mock:
        print(f"Module paths resolved:")
        for name, m in [
            ("ExpAgent", modules.expagent),
            ("CodingAgent", modules.codingagent),
            ("ReproAgent", modules.reproagent),
        ]:
            print(f"  {name}: {m.path} (via {m.source})")

    planner = Planner(
        api_base=config.llm.api_base,
        api_key_env=config.llm.api_key_env,
        model=config.llm.model,
        mock=mock,
    )

    expagent_path = modules.expagent.path if not mock else ""
    codingagent_path = modules.codingagent.path if not mock else ""
    reproagent_path = modules.reproagent.path if not mock else ""

    return Controller(
        planner=planner,
        shared_workspace=config.policy.shared_workspace,
        expagent=ExpAgentAdapter(
            module_path=expagent_path,
            model=config.llm.model,
            api_base=config.llm.api_base,
            api_key_env=config.llm.api_key_env,
            mock=mock,
        ),
        codingagent=CodingAgentAdapter(
            module_path=codingagent_path,
            model=config.llm.model,
            api_base=config.llm.api_base,
            api_key_env=config.llm.api_key_env,
            max_steps=48,
            mock=mock,
        ),
        reproagent=ReproAgentAdapter(
            module_path=reproagent_path,
            model=config.llm.model,
            api_base=config.llm.api_base,
            api_key_env=config.llm.api_key_env,
            mirror_profile=config.policy.repro_mirror_profile,
            dataset_cache_dir=config.policy.repro_dataset_cache,
            mock=mock,
        ),
    )


def run_loop(
    state: ResearchState,
    controller: Controller,
    max_steps: int = 50,
) -> ResearchState:
    """Run the agentic loop to completion (or max steps)."""
    return controller.run(state, max_steps=max_steps)


def resume_run(workspace_dir: str, run_id: str) -> ResearchState | None:
    """Load an existing run state."""
    return load_state(workspace_dir, run_id)


def status(workspace_dir: str, run_id: str) -> str:
    """Return a human-readable status string."""
    state = load_state(workspace_dir, run_id)
    if state is None:
        return f"No run found at {workspace_dir}/{run_id}"

    tasks_total = len(state.tasks)
    tasks_done = sum(1 for t in state.tasks if t.status.value == "completed")
    tasks_failed = sum(1 for t in state.tasks if t.status.value == "failed")
    tasks_pending = sum(1 for t in state.tasks if t.status.value == "pending")

    return (
        f"Run: {state.run.run_id}\n"
        f"Status: {state.run.status.value}\n"
        f"Goal: {state.run.research_goal[:120]}\n"
        f"Tasks: {tasks_total} total, {tasks_done} done, "
        f"{tasks_failed} failed, {tasks_pending} pending\n"
        f"Artifacts: {len(state.artifacts)}\n"
        f"Decisions: {len(state.decisions)}\n"
        f"Budget: {state.budget.tasks_run}/{state.budget.max_tasks} tasks, "
        f"{state.budget.api_calls_used}/{state.budget.max_api_calls} api calls\n"
        f"Summary: {state.current_summary[:200]}"
    )
