"""Capability registry — loads ExpertCards ("business cards") for sub-agents.

See docs/CONVERSATION_LAYER_DESIGN.md §4.4 / §5.

Precedence (low -> high):
    built-in defaults  <  config.yaml agents.cards.<name>  <  <module>/agent.yaml

The registry is deliberately thin: it loads cards, validates them, and tells
the chat layer what may be called at which commitment tier. It is NOT an
agent framework.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .conversation.models import ExpertCard
from .config import Config

# ── Built-in default cards ────────────────────────────────────────────────────
# These guarantee a working system out of the box. They are overridden by
# each module's own agent.yaml once that repo ships one.

BUILTIN_CARDS: list[dict[str, Any]] = [
    {
        "name": "expagent",
        "role": "scientific_advisor",
        "description_for_router": (
            "科学顾问。擅长：科学原理问答、研究 idea 可行性讨论、实验设计、"
            "实验结果分析、失败归因。只读咨询，不执行代码、不跑实验。"
            "适用：用户问原理/方法/文献，或讨论模糊想法。"
            "不适用：需要实际改代码或跑实验的请求。"
        ),
        "capabilities": [
            "scientific_advisory", "idea_discussion",
            "experiment_design", "result_analysis",
        ],
        "side_effects": "none",
        "input_contract": "advise(AdvisorContext) -> ScientificDecision",
        "status": "available",
    },
    {
        "name": "codingagent",
        "role": "coding_agent",
        "description_for_router": (
            "程序员。擅长：repo 级代码修改（补日志、修 bug、加配置、API 兼容修复）。"
            "适用：明确的代码修改任务。代码修改有副作用，只能在 ResearchRun 内执行——"
            "对话层收到修改类请求时，应引导用户立项或推进已有 run，不要直接调用。"
        ),
        "capabilities": ["code_modification"],
        "side_effects": "workspace",
        "input_contract": "run_code_task(CodeTaskSpec) -> PatchReport",
        "status": "available",
    },
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
    {
        "name": "reproagent",
        "role": "reproduction_agent",
        "description_for_router": (
            "复现工程师。克隆论文仓库、建 conda 环境、跑 baseline 实验。"
            "仅在用户明确要求复现/跑 baseline 时使用，且只能在 ResearchRun 内执行。"
            "绝不用来回答问题。"
        ),
        "capabilities": ["reproduction_task", "baseline_run"],
        "side_effects": "workspace_and_environment",
        "input_contract": "run_controller(ReproTask) -> AgentState",
        "status": "available",
    },
]

_ALLOWED_SIDE_EFFECTS = {"none", "workspace", "workspace_and_environment"}


class CapabilityRegistry:
    """Loads and serves ExpertCards."""

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
            card_path = Path(module_path) / "agent.yaml"
            if card_path.exists():
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
