"""Capability registry — the single source of truth for capability routing.

See docs/reference/SCIENTIFIC_ORCHESTRATION_MAINLINE_REDESIGN.md §6 / §B.

Both the chat router and the research controller resolve capabilities from
this registry, so there is exactly ONE capability vocabulary and ONE
capability→executor mapping. Sub-modules declare their own capabilities in
their own `agent.yaml`; ResAgent never hard-codes a second copy.

Precedence (low -> high):
    built-in defaults  <  config.yaml agents.cards.<name>  <  <module>/agent.yaml

The V2 capability vocabulary is frozen:

    modify_code           -> CodingAgent
    reproduce_experiment  -> ReproAgent
    execute_experiment    -> ReproAgent
    analyze_results       -> ExpAgent
    search_literature     -> ExpAgent
    ask_user              -> ResAgent (built-in, not a sub-module)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import Producer
from .conversation.models import ExpertCard
from .config import Config


class CapabilityError(Exception):
    """Raised when capability routing cannot be resolved deterministically."""


# Frozen V2 scientific-capability vocabulary. Every sub-module agent.yaml must
# declare only capabilities it OWNS; a duplicate declaration is a config error.
V2_CAPABILITIES = (
    "modify_code",
    "reproduce_experiment",
    "execute_experiment",
    "analyze_results",
    "search_literature",
    "ask_user",
)

# Module card name -> Producer. ResAgent's own ask_user is built-in.
_MODULE_TO_PRODUCER: dict[str, Producer] = {
    "expagent": Producer.ExpAgent,
    "codingagent": Producer.CodingAgent,
    "reproagent": Producer.ReproAgent,
    "resagent": Producer.ResAgent,
}

# ── Built-in default cards ────────────────────────────────────────────────────
# Only ResAgent-owned cards live here. The three executor modules (ExpAgent,
# CodingAgent, ReproAgent) declare their capabilities in their OWN agent.yaml,
# loaded from the configured module paths. Keeping a second copy of those
# capability tables here would let the router drift from the real modules.

BUILTIN_CARDS: list[dict[str, Any]] = [
    {
        "name": "codingagent_qa",
        "role": "coding_advisor",
        "description_for_router": (
            "代码理解问答（只读）。回答关于某个 repo 的代码问题：训练入口在哪、"
            "loss 怎么算、模型结构定义在哪、报错可能是哪行导致的。"
            "回答附文件/行号证据，不修改任何文件。"
            "调用时必须在 params 中提供 workspace_path（目标 repo 的绝对路径）；"
            "如果用户没给路径，先用 reply 追问，不要猜。"
        ),
        "capabilities": ["code_question"],
        "side_effects": "none",
        "input_contract": "run_code_question(CodeQuestionSpec) -> CodeExplanation",
        "status": "available",
    },
]

_ALLOWED_SIDE_EFFECTS = {"none", "workspace", "workspace_and_environment"}


def _find_agent_card(module_path: Path) -> Path | None:
    """Find a module card from either a repo root or an import package path."""
    candidates = [module_path, *list(module_path.parents)[:3]]
    for candidate in candidates:
        card = candidate / "agent.yaml"
        if card.is_file():
            return card
    return None


class CapabilityRegistry:
    """Loads and serves ExpertCards; resolves capabilities deterministically."""

    def __init__(self, config: Config):
        self.config = config
        self.cards: dict[str, ExpertCard] = {}
        self.warnings: list[str] = []

    # ── loading ───────────────────────────────────────────────────────────

    def load(self) -> None:
        self.cards = {}
        self.warnings = []

        for raw in BUILTIN_CARDS:
            card = ExpertCard.model_validate(raw)
            self.cards[card.name] = card

        # config.yaml agents.cards.<name> override
        for name, raw in (self.config.agents.cards or {}).items():
            self._apply_card(name, raw, source="config")

        # repo agent.yaml override (highest precedence)
        for module_path in self._module_paths():
            card_path = _find_agent_card(Path(module_path))
            if card_path is not None:
                self._load_repo_card(card_path)

    def _module_paths(self) -> list[str]:
        paths = [
            self.config.agents.expagent,
            self.config.agents.codingagent,
            self.config.agents.reproagent,
        ]
        return [p for p in paths if p]

    def _load_repo_card(self, card_path: Path) -> None:
        try:
            import yaml
            data = yaml.safe_load(card_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            self.warnings.append(f"Failed to parse {card_path}: {e}; keeping existing card.")
            return
        if not isinstance(data, dict) or not data.get("name"):
            self.warnings.append(f"{card_path} has no 'name'; keeping existing card.")
            return
        self._apply_card(data["name"], data, source=str(card_path))

    def _apply_card(self, name: str, raw: dict, source: str) -> None:
        raw = dict(raw)
        raw["name"] = name
        # Tolerate non-standard side_effects vocab from repo cards: coerce to
        # the safe middle value so the router stays conservative.
        se = raw.get("side_effects")
        if se is not None and se not in _ALLOWED_SIDE_EFFECTS:
            self.warnings.append(
                f"Card '{name}' ({source}): side_effects={se!r} not in "
                f"{sorted(_ALLOWED_SIDE_EFFECTS)}; coerced to 'workspace'."
            )
            raw["side_effects"] = "workspace"
        try:
            card = ExpertCard.model_validate(raw)
        except Exception as e:
            self.warnings.append(f"Card '{name}' ({source}) invalid: {e}; keeping existing.")
            return
        # Config/repo partial overrides inherit missing fields from existing card.
        if name in self.cards and source != "builtin":
            merged = self.cards[name].model_dump()
            merged.update({k: v for k, v in raw.items() if v not in (None, "", [])})
            card = ExpertCard.model_validate(merged)
        self.cards[name] = card

    # ── queries ───────────────────────────────────────────────────────────

    def get(self, name: str) -> ExpertCard | None:
        return self.cards.get(name)

    def resolve(self, capability: str) -> Producer:
        """Deterministically resolve a scientific capability to its executor.

        `ask_user` is a ResAgent built-in. Every other capability must be
        declared by exactly one available sub-module. Routing is derived from
        that card owner, never from a second capability-to-executor table.
        """
        if capability == "ask_user":
            return Producer.ResAgent

        if capability not in V2_CAPABILITIES:
            raise CapabilityError(f"unknown capability {capability!r}")

        owners = [
            name for name, card in self.cards.items()
            if capability in card.capabilities
        ]
        if not owners:
            raise CapabilityError(
                f"capability '{capability}' is not declared by any module"
            )
        if len(owners) > 1:
            raise CapabilityError(
                f"capability '{capability}' is declared by multiple modules: "
                f"{sorted(owners)}"
            )
        owner = owners[0]
        card = self.cards[owner]
        if card.status != "available":
            raise CapabilityError(
                f"capability '{capability}' owner '{owner}' is {card.status!r}"
            )
        producer = _MODULE_TO_PRODUCER.get(owner)
        if producer is None:
            raise CapabilityError(
                f"capability '{capability}' owner '{owner}' has no Producer mapping"
            )
        return producer

    def validate_scientific_routing(self) -> None:
        """Validate the complete V2 registry before a production run starts."""
        issues: list[str] = []
        for capability in V2_CAPABILITIES:
            if capability == "ask_user":
                continue
            try:
                self.resolve(capability)
            except CapabilityError as exc:
                issues.append(str(exc))
        if issues:
            raise CapabilityError(
                "invalid scientific capability registry: " + "; ".join(issues)
            )

    def capability_owner(self, capability: str) -> str:
        """Return the module card name that owns a capability ("" if none)."""
        owners = [
            name for name, card in self.cards.items()
            if capability in card.capabilities
        ]
        return owners[0] if len(owners) == 1 else ""

    def controller_summary(self) -> str:
        """Compact capability→executor table for the controller prompt.

        Deterministic and non-raising: unresolved capabilities are reported
        as such here, while actual dispatch fails closed via `resolve()`.
        """
        lines = ["## Capability Routing (source of truth: module agent.yaml)"]
        for capability in V2_CAPABILITIES:
            if capability == "ask_user":
                lines.append(f"- {capability} -> ResAgent (built-in)")
                continue
            owner = self.capability_owner(capability)
            producer = _MODULE_TO_PRODUCER.get(owner)
            if producer is None:
                lines.append(
                    f"- {capability} -> UNRESOLVED "
                    f"(owner: {owner or 'none'})"
                )
            else:
                lines.append(f"- {capability} -> {producer.value} (module: {owner})")
        return "\n".join(lines)

    def available(self) -> list[ExpertCard]:
        return [c for c in self.cards.values() if c.status == "available"]

    def router_descriptions(self) -> str:
        return "\n".join(c.router_line() for c in self.cards.values())

    def check_callable(self, name: str, tier: int) -> tuple[bool, str]:
        """Whether the chat layer may call this expert at the given tier.

        Tier 1 (advisory consult): requires status == available and
        side_effects == none. Tier 2 actions go through research runs and
        are gated by user confirmation instead.
        """
        card = self.cards.get(name)
        if card is None:
            return False, f"Unknown expert: {name}. Known: {sorted(self.cards)}"
        if card.status == "planned":
            return False, (
                f"Expert '{name}' is planned but not available yet. "
                f"Tell the user this capability is coming and offer alternatives."
            )
        if card.status != "available":
            return False, f"Expert '{name}' is currently {card.status}."
        if tier <= 1 and card.side_effects != "none":
            return False, (
                f"Expert '{name}' has side_effects={card.side_effects}; it can only "
                f"run inside a confirmed ResearchRun (propose_research_run / advance_run)."
            )
        return True, ""
