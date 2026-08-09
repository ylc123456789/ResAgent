"""Config loading from yaml + env overrides. xxx"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentPaths:
    expagent: str = ""
    codingagent: str = ""
    reproagent: str = ""
    cards: dict = field(default_factory=dict)  # agents.cards.<name> overrides


@dataclass
class LLMConfig:
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = "deepseek-v4-pro"


@dataclass
class WorkspaceConfig:
    default_runs_dir: str = "runs"


@dataclass
class PolicyConfig:
    max_task_retries: int = 2
    confirm_before_external_runs: bool = True
    confirm_before_long_tasks: bool = True
    repro_mirror_profile: str = "none"  # "none" | "cn" | "autodl"


@dataclass
class ChatConfig:
    """Conversation-layer settings (docs/CONVERSATION_LAYER_DESIGN.md §4.9)."""
    max_tool_calls_per_turn: int = 4
    default_advance_steps: int = 3
    max_steps_per_turn: int = 5
    consult_max_steps: int = 12  # step cap passed to expert advisory calls
    conversations_dirname: str = "conversations"


@dataclass
class Config:
    agents: AgentPaths = field(default_factory=AgentPaths)
    llm: LLMConfig = field(default_factory=LLMConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)

    # External module command overrides (for subprocess fallback).
    # Filled from module_paths resolution.
    cmd_expagent: str = ""
    cmd_codingagent: str = ""
    cmd_reproagent: str = ""


def load_config(path: str = "") -> Config:
    """Load config from yaml file, falling back to defaults + env vars."""
    cfg = Config()

    # Try to load yaml
    yaml_path = _find_config_yaml(path)
    if yaml_path:
        _apply_yaml(cfg, yaml_path)

    # Env var overrides
    if os.environ.get("RESAGENT_WORKSPACE"):
        cfg.workspace.default_runs_dir = os.environ["RESAGENT_WORKSPACE"]
    if os.environ.get("EXPAGENT_PATH"):
        cfg.agents.expagent = os.environ["EXPAGENT_PATH"]
    if os.environ.get("CODINGAGENT_PATH"):
        cfg.agents.codingagent = os.environ["CODINGAGENT_PATH"]
    if os.environ.get("REPROAGENT_PATH"):
        cfg.agents.reproagent = os.environ["REPROAGENT_PATH"]
    if os.environ.get("RESAGENT_MODEL"):
        cfg.llm.model = os.environ["RESAGENT_MODEL"]

    return cfg


def _find_config_yaml(explicit_path: str) -> str | None:
    if explicit_path and Path(explicit_path).exists():
        return explicit_path
    cwd_cfg = Path("config.yaml")
    if cwd_cfg.exists():
        return str(cwd_cfg)
    return None


def _apply_yaml(cfg: Config, path: str) -> None:
    try:
        import yaml
    except ImportError:
        return
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    agents = data.get("agents", {})
    if isinstance(agents, dict):
        cfg.agents.expagent = agents.get("expagent_path", cfg.agents.expagent)
        cfg.agents.codingagent = agents.get("codingagent_path", cfg.agents.codingagent)
        cfg.agents.reproagent = agents.get("reproagent_path", cfg.agents.reproagent)
        cards = agents.get("cards", {})
        if isinstance(cards, dict):
            cfg.agents.cards = cards

    llm = data.get("llm", {})
    if isinstance(llm, dict):
        cfg.llm.api_base = llm.get("api_base", cfg.llm.api_base)
        cfg.llm.api_key_env = llm.get("api_key_env", cfg.llm.api_key_env)
        cfg.llm.model = llm.get("model", cfg.llm.model)

    ws = data.get("workspace", {})
    if isinstance(ws, dict):
        cfg.workspace.default_runs_dir = ws.get("default_runs_dir", cfg.workspace.default_runs_dir)

    pol = data.get("policy", {})
    if isinstance(pol, dict):
        cfg.policy.max_task_retries = pol.get("max_task_retries", cfg.policy.max_task_retries)
        cfg.policy.confirm_before_external_runs = pol.get(
            "confirm_before_external_runs", cfg.policy.confirm_before_external_runs
        )
        cfg.policy.confirm_before_long_tasks = pol.get(
            "confirm_before_long_tasks", cfg.policy.confirm_before_long_tasks
        )
        cfg.policy.repro_mirror_profile = pol.get(
            "repro_mirror_profile", cfg.policy.repro_mirror_profile
        )

    chat = data.get("chat", {})
    if isinstance(chat, dict):
        for key in ("max_tool_calls_per_turn", "default_advance_steps",
                    "max_steps_per_turn", "consult_max_steps", "conversations_dirname"):
            if key in chat:
                setattr(cfg.chat, key, chat[key])
