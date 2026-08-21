"""High-level orchestration helpers — init, resume, run lifecycle."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import AgentKind, AgentTask, Producer, ResearchState
from .persistence.state import init_state, save_state, load_state
from .config import Config, load_config
from .capabilities import CapabilityRegistry
from .integrations.module_paths import resolve_all
from .controller.planner import Planner
from .controller import Controller
from .controller.tasks import create_task
from .adapters.expagent import ExpAgentAdapter
from .adapters.codingagent import CodingAgentAdapter
from .adapters.reproagent import ReproAgentAdapter
from .persistence.report import generate_all


def _generate_run_id() -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6]
    return f"res-{today}-{suffix}"


def seed_initial_advisory_task(state: ResearchState) -> AgentTask:
    """Register the deterministic initial ExpAgent advisory task.

    The initial scientific consultation is expressed as a registered task (not
    a free-floating planner hint) so ``allowed_action_candidates`` only ever
    exposes real, task-bound actions.
    """
    task = create_task(
        state,
        source="resagent",
        agent=Producer.ExpAgent,
        kind=AgentKind.advise,
        required=True,
        action_id="initial_consult",
        input={
            "description": (
                "Initial scientific consultation to produce the action graph."
            ),
            "task_goal": (
                "Analyze the research goal and propose the scientific "
                "action graph."
            ),
        },
    )
    return task


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
    seed_initial_advisory_task(state)
    save_state(state)
    return state


def build_capability_registry(config: Config, modules=None) -> CapabilityRegistry:
    """Build THE capability registry — the single construction path.

    Both the chat router and the research controller obtain their registry
    here, so capability routing can never drift between the two. Module
    paths come from the same 5-tier resolution the controller uses
    (CLI > env > config > import > vendor); unresolvable modules degrade
    to an empty path, leaving built-in/config cards in place.
    """
    if modules is None:
        modules = resolve_all(
            cli_expagent=config.cmd_expagent,
            cli_codingagent=config.cmd_codingagent,
            cli_reproagent=config.cmd_reproagent,
            config_expagent=config.agents.expagent,
            config_codingagent=config.agents.codingagent,
            config_reproagent=config.agents.reproagent,
        )
    registry = CapabilityRegistry(_resolved_registry_config(config, modules))
    registry.load()
    registry.validate_scientific_routing()
    return registry


def build_controller(
    config: Config, mock: bool = False, registry: CapabilityRegistry | None = None,
) -> Controller:
    """Build a Controller from config, resolving all module paths.

    Uses the 5-tier path resolution: CLI > env > config > import > vendor.
    Paths from config.agents are those already resolved through CLI/env/config.
    Callers that need to share one registry with the chat layer (main.py
    chat) build it via build_capability_registry and pass it in.
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

    expagent_path = modules.expagent.path if not mock else ""
    codingagent_path = modules.codingagent.path if not mock else ""
    reproagent_path = modules.reproagent.path if not mock else ""

    # Unified capability registry — the single source of truth shared by the
    # chat router and the research controller, built from the RESOLVED module
    # paths via the single construction path (build_capability_registry).
    # In mock mode without a caller-provided registry there are no module
    # checkouts, so the adapters fall back to the frozen V2 vocabulary.
    if registry is None:
        registry = build_capability_registry(config, modules)
        for warning in registry.warnings:
            print(f"[registry] {warning}", file=sys.stderr)

    planner = Planner(
        api_base=config.llm.api_base,
        api_key_env=config.llm.api_key_env,
        model=config.llm.model,
        mock=mock,
        registry=registry,
    )

    return Controller(
        planner=planner,
        shared_workspace=config.policy.shared_workspace,
        resources=config.resources,
        expagent=ExpAgentAdapter(
            module_path=expagent_path,
            model=config.llm.model,
            api_base=config.llm.api_base,
            api_key_env=config.llm.api_key_env,
            mock=mock,
            registry=registry,
        ),
        codingagent=CodingAgentAdapter(
            module_path=codingagent_path,
            model=config.llm.model,
            api_base=config.llm.api_base,
            api_key_env=config.llm.api_key_env,
            max_steps=48,
            resource_root=config.resources.root,
            dataset_cache_dir=config.policy.repro_dataset_cache,
            mirror_profile=config.policy.repro_mirror_profile,
            pip_index_profile=config.policy.repro_mirror_profile,
            mock=mock,
        ),
        reproagent=ReproAgentAdapter(
            module_path=reproagent_path,
            model=config.llm.model,
            api_base=config.llm.api_base,
            api_key_env=config.llm.api_key_env,
            mirror_profile=config.policy.repro_mirror_profile,
            dataset_cache_dir=config.policy.repro_dataset_cache,
            resource_root=config.resources.root,
            reuse_mode=config.resources.reuse_mode,
            mock=mock,
        ),
    )


def _resolved_registry_config(config: Config, modules) -> Config:
    """Return a Config whose agent paths reflect the resolved module checkouts."""
    import copy

    reg_config = copy.copy(config)
    reg_config.agents = copy.copy(config.agents)
    reg_config.agents.expagent = modules.expagent.path
    reg_config.agents.codingagent = modules.codingagent.path
    reg_config.agents.reproagent = modules.reproagent.path
    return reg_config


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
