"""ResAgent controller package with lazy public exports.

Contracts and prompts are imported by lower-level modules, so importing this
package must not eagerly initialize the controller loop.
"""

from __future__ import annotations

__all__ = ["Controller", "PlannedAction", "Planner"]


def __getattr__(name: str):
    if name == "Controller":
        from .loop import Controller
        return Controller
    if name in {"PlannedAction", "Planner"}:
        from .planner import PlannedAction, Planner
        return {"PlannedAction": PlannedAction, "Planner": Planner}[name]
    raise AttributeError(name)
