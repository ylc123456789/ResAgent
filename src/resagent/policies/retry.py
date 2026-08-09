"""Retry policy — deterministic transient classification + retry budget."""

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
        return classify_transient(error).get("category") == "transient"

    def can_retry(self, task: AgentTask) -> bool:
        """Check if task still has retry budget."""
        return len(task.attempts) < self.max_retries


def classify_transient(error: str) -> dict:
    """Deterministic classification of known transport/network errors.

    Returns a dict with category, confidence, and recommended_action.
    Falls back to "unknown" so the LLM classifier can take over.
    """
    lower = error.lower()

    transport_markers = {
        "transient": [
            # Git / GitHub
            "fatal: unable to access", "failed to connect to github.com",
            "could not resolve host", "connection timed out",
            "gnutls_handshake", "ssl", "tls", "eof occurred in violation of protocol",
            # HTTP
            "502 bad gateway", "503 service unavailable", "504 gateway timeout",
            "429 too many requests", "rate limit",
            # Network
            "connection refused", "connection reset", "network is unreachable",
            "temporary failure in name resolution", "name resolution",
            "no route to host", "broken pipe",
            # Generic
            "download failed", "timeout", "try again",
            "resource temporarily unavailable",
        ],
    }

    for marker in transport_markers["transient"]:
        if marker in lower:
            return {
                "category": "transient",
                "confidence": "high",
                "explanation": f"Deterministic match: '{marker}' found in error text.",
                "recommended_action": "retry",
            }

    return {
        "category": "unknown",
        "confidence": "low",
        "explanation": "No known transient marker matched.",
        "recommended_action": "investigate",
    }
