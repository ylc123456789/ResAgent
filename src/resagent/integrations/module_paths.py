"""Resolve module paths with 5-tier priority:

    1. CLI arg (passed in explicitly)
    2. Environment variable
    3. Config file (config.yaml)
    4. Importable package (Python import)
    5. Vendored fallback (local copy inside ResAgent)

Every resolution records its source so observability is built in.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModuleResolution:
    path: str
    source: str  # "cli" | "env" | "config" | "import" | "vendor" | "not_found"


@dataclass
class ResolvedModules:
    expagent: ModuleResolution = field(default_factory=lambda: ModuleResolution("", "not_found"))
    codingagent: ModuleResolution = field(default_factory=lambda: ModuleResolution("", "not_found"))
    reproagent: ModuleResolution = field(default_factory=lambda: ModuleResolution("", "not_found"))


# ── env var names ─────────────────────────────────────────────────────────────

ENV_VARS = {
    "expagent": "EXPAGENT_PATH",
    "codingagent": "CODINGAGENT_PATH",
    "reproagent": "REPROAGENT_PATH",
}

IMPORT_NAMES = {
    "expagent": "experiment_designer",
    "codingagent": "coding_agent",
    "reproagent": "reproagent",
}

VENDOR_DIRS = {
    "expagent": "vendor/ExpAgent",
    "codingagent": "vendor/CodingAgent",
    "reproagent": "vendor/reproagent",
}


def resolve_all(
    cli_expagent: str = "",
    cli_codingagent: str = "",
    cli_reproagent: str = "",
    config_expagent: str = "",
    config_codingagent: str = "",
    config_reproagent: str = "",
) -> ResolvedModules:
    """Run full 5-tier resolution for all three modules."""
    return ResolvedModules(
        expagent=_resolve_one("expagent", cli_expagent, config_expagent),
        codingagent=_resolve_one("codingagent", cli_codingagent, config_codingagent),
        reproagent=_resolve_one("reproagent", cli_reproagent, config_reproagent),
    )


def _resolve_one(name: str, cli: str, config: str) -> ModuleResolution:
    # 1. CLI arg
    if cli and Path(cli).exists():
        return ModuleResolution(path=cli, source="cli")

    # 2. Env var
    env_val = os.environ.get(ENV_VARS[name], "")
    if env_val and Path(env_val).exists():
        return ModuleResolution(path=env_val, source="env")

    # 3. Config file
    if config and Path(config).exists():
        return ModuleResolution(path=config, source="config")

    # 4. Importable package
    pkg = IMPORT_NAMES.get(name, "")
    if pkg:
        try:
            mod = importlib.import_module(pkg)
            pkg_path = getattr(mod, "__path__", [None])[0] or getattr(mod, "__file__", "")
            if pkg_path:
                pkg_path = str(Path(pkg_path).resolve())
                return ModuleResolution(path=pkg_path, source="import")
        except ImportError:
            pass

    # 5. Vendored fallback
    vendor = str(Path(__file__).resolve().parent.parent.parent / VENDOR_DIRS.get(name, ""))
    if Path(vendor).exists():
        return ModuleResolution(path=vendor, source="vendor")

    return ModuleResolution(path="", source="not_found")
