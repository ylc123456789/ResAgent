"""Retry policy — handles transient task failures."""

from __future__ import annotations

from ..models import AgentTask


class RetryPolicy:
    """Decides whether a failed task should be retried."""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries

    def should_retry(self, task: AgentTask, error: str) -> bool:
        """Check if task should be retried based on error type and attempt count."""
        if len(task.attempts) >= self.max_retries:
            return False
        return _is_transient(error)

    def can_retry(self, task: AgentTask) -> bool:
        """Check if task still has retry budget."""
        return len(task.attempts) < self.max_retries


def _is_transient(error: str) -> bool:
    """Heuristic: is this error likely transient?"""
    lower = error.lower()
    transient_markers = [
        "timeout", "connection refused", "connection reset",
        "temporary failure", "rate limit", "too many requests",
        "5xx", "502", "503", "504", "gateway", "unavailable",
        "network", "dns", "download failed", "try again",
        "resource temporarily unavailable",
    ]
    return any(m in lower for m in transient_markers)
