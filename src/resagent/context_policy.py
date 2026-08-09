"""Model-aware limits for packing ResAgent controller context."""
from __future__ import annotations

from dataclasses import dataclass

MODEL_CONTEXT_WINDOWS = {
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 64_000,
    "gpt-4.1": 1_000_000,
    "gpt-4o": 128_000,
}


@dataclass(frozen=True)
class ContextPolicy:
    context_window_tokens: int
    input_budget_tokens: int
    artifact_count: int
    artifact_summary_chars: int
    latest_result_chars: int
    observation_count: int
    observation_chars: int

    @classmethod
    def for_model(cls, model: str | None) -> "ContextPolicy":
        name = (model or "").lower().split("/")[-1]
        window = MODEL_CONTEXT_WINDOWS.get(name, 128_000)
        budget = max(8_000, window - max(8_000, window // 10))
        if window >= 500_000:
            return cls(window, budget, 20, 2_000, 16_000, 30, 2_000)
        if window >= 128_000:
            return cls(window, budget, 12, 800, 8_000, 15, 800)
        return cls(window, budget, 8, 400, 4_000, 8, 400)
