"""Context window policy — model-aware artifact/history selection.

MVP: passes everything unfiltered.
Future: apply model-specific token budgets, truncation, and summarization.
"""

from __future__ import annotations

from .models import ResearchState


class ContextPolicy:
    """Selects and trims state content to fit model context windows."""

    def __init__(
        self,
        max_tokens: int = 128_000,
        reserve_for_prompt: int = 8_000,
        reserve_for_response: int = 4_000,
    ):
        self.max_tokens = max_tokens
        self._budget = max_tokens - reserve_for_prompt - reserve_for_response

    def available_tokens(self) -> int:
        return self._budget

    def fit_artifacts(self, state: ResearchState, limit: int = 20) -> int:
        """Return how many recent artifacts can fit. MVP: just returns limit."""
        return min(limit, len(state.artifacts))

    def fit_observations(self, state: ResearchState, limit: int = 20) -> int:
        """Return how many recent observations can fit. MVP: just returns limit."""
        return min(limit, len(state.observations))

    def fit_tasks(self, state: ResearchState) -> int:
        """Return how many tasks to include. MVP: all of them."""
        return len(state.tasks)
