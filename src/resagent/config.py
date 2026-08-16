"""Config loading from yaml + env overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentPaths:
    """Paths to the expagent/codingagent/reproagent module checkouts."""
    expagent: str = ""
    codingagent: str = ""
    reproagent: str = ""
    cards: dict = field(default_factory=dict)  # agents.cards.<name> overrides


@dataclass
class LLMConfig:
    """LLM endpoint + model shared by all sub-agents."""
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = "deepseek-v4-pro"


@dataclass
class WorkspaceConfig:
    """Workspace-level paths (default runs directory)."""
    default_runs_dir: str = "runs"


@dataclass
class PolicyConfig:
    """Policy knobs: retries, confirmation gates, repro mirror/cache."""
    max_task_retries: int = 2
    confirm_before_external_runs: bool = True
    confirm_before_long_tasks: bool = True
    shared_workspace: str = "auto"  # "auto" | "always" | "never"
    repro_mirror_profile: str = "none"  # "none" | "cn" | "autodl"
    repro_dataset_cache: str = ""       # e.g. /root/autodl-tmp/datasets (server)


@dataclass
class ResourcesConfig:
    """M2 resource management (contracts/ENVIRONMENT_*_V1).

    Default is fully legacy: no resource root, name-based reuse, no cleanup.
    Content-addressed mode engages only when root is set AND
    reuse_mode == "content_addressed".
    """
    root: str = ""                       # resource root (manifests/locks/envs)
    reuse_mode: str = "legacy"           # "legacy" | "content_addressed"
    cleanup_enabled: bool = False
    cleanup_max_bytes: int = 0
    cleanup_min_unused_days: int = 30


@dataclass
class ChatConfig:
    """Conversation-layer settings (docs/reference/CONVERSATION_LAYER_DESIGN.md §4.9)."""
    max_tool_calls_per_turn: int = 4
    default_advance_steps: int = 3
    max_steps_per_turn: int = 5
    consult_max_steps: int = 12  # step cap passed to expert advisory calls
    conversations_dirname: str = "conversations"


@dataclass
class Config:
    """Top-level config aggregating all sections, plus CLI path overrides."""
    agents: AgentPaths = field(default_factory=AgentPaths)
    llm: LLMConfig = field(default_factory=LLMConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)

    # Optional CLI-override inputs to module_paths resolution (NOT populated by resolution).
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
    if os.environ.get("RESAGENT_RESOURCE_ROOT"):
        cfg.resources.root = os.environ["RESAGENT_RESOURCE_ROOT"]
    if os.environ.get("EXPAGENT_PATH"):
        cfg.agents.expagent = os.environ["EXPAGENT_PATH"]
    if os.environ.get("CODINGAGENT_PATH"):
        cfg.agents.codingagent = os.environ["CODINGAGENT_PATH"]
    if os.environ.get("REPROAGENT_PATH"):
        cfg.agents.reproagent = os.environ["REPROAGENT_PATH"]
    if os.environ.get("RESAGENT_MODEL"):
        cfg.llm.model = os.environ["RESAGENT_MODEL"]
    if os.environ.get("REPROAGENT_DATASET_CACHE"):
        # Honor reproagent's own env-var convention
        cfg.policy.repro_dataset_cache = os.environ["REPROAGENT_DATASET_CACHE"]

    return cfg


def _find_config_yaml(explicit_path: str) -> str | None:
    """Resolve the config yaml path (explicit arg, else ./config.yaml, else None)."""
    if explicit_path and Path(explicit_path).exists():
        return explicit_path
    cwd_cfg = Path("config.yaml")
    if cwd_cfg.exists():
        return str(cwd_cfg)
    return None


def _apply_yaml(cfg: Config, path: str) -> None:
    """Merge yaml values into the Config object."""
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
        cfg.policy.shared_workspace = pol.get(
            "shared_workspace", cfg.policy.shared_workspace
        )
        cfg.policy.repro_mirror_profile = pol.get(
            "repro_mirror_profile", cfg.policy.repro_mirror_profile
        )
        cfg.policy.repro_dataset_cache = pol.get(
            "repro_dataset_cache", cfg.policy.repro_dataset_cache
        )

    chat = data.get("chat", {})
    if isinstance(chat, dict):
        for key in ("max_tool_calls_per_turn", "default_advance_steps",
                    "max_steps_per_turn", "consult_max_steps", "conversations_dirname"):
            if key in chat:
                setattr(cfg.chat, key, chat[key])

    res = data.get("resources", {})
    if isinstance(res, dict):
        cfg.resources.root = res.get("root", cfg.resources.root)
        cfg.resources.reuse_mode = res.get("reuse_mode", cfg.resources.reuse_mode)
        cleanup = res.get("cleanup", {})
        if isinstance(cleanup, dict):
            cfg.resources.cleanup_enabled = cleanup.get("enabled", cfg.resources.cleanup_enabled)
            cfg.resources.cleanup_max_bytes = cleanup.get("max_bytes", cfg.resources.cleanup_max_bytes)
            cfg.resources.cleanup_min_unused_days = cleanup.get(
                "min_unused_days", cfg.resources.cleanup_min_unused_days
            )
