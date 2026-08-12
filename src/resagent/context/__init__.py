"""Context builders and model-aware packing policy."""

from .builder import (
    build_codingagent_context,
    build_controller_context,
    build_expagent_context,
    build_reproagent_context,
)
from .policy import ContextPolicy, MODEL_CONTEXT_WINDOWS

__all__ = [
    "ContextPolicy", "MODEL_CONTEXT_WINDOWS", "build_codingagent_context",
    "build_controller_context", "build_expagent_context", "build_reproagent_context",
]
