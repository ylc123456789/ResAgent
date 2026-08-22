"""Single construction path for ResAgent tasks."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..models import (
    AgentKind,
    AgentTask,
    Producer,
    ResearchState,
    TaskPriority,
)


def task_fingerprint(
    executor: Producer, capability: str, payload: dict[str, Any],
) -> str:
    """Return a stable semantic identity for deduplicating planned work."""
    ignored = {"description", "_retry_scheduled"}
    normalized = {
        key: payload[key] for key in sorted(payload)
        if key not in ignored and payload[key] not in ("", [], None)
    }
    raw = json.dumps(
        {
            "executor": executor.value,
            "capability": capability,
            "input": normalized,
        },
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def create_task(
    state: ResearchState,
    *,
    source: str,
    agent: Producer,
    kind: AgentKind,
    capability: str = "",
    action_id: str = "",
    project_ref: str = "",
    depends_on: list[str] | None = None,
    input: dict[str, Any] | None = None,
    priority: TaskPriority = TaskPriority.medium,
    required: bool = True,
    analysis_required: bool | None = None,
    fingerprint: str = "",
    append: bool = True,
    task_number: int | None = None,
) -> AgentTask:
    """Build one complete task and optionally append it to ``state``.

    Batch graph conversion uses ``append=False`` so it can validate the whole
    graph before committing it. All other production paths append directly.
    """
    if agent in {Producer.CodingAgent, Producer.ReproAgent} and not capability:
        raise ValueError(f"{agent.value} task requires a capability")

    task = AgentTask(
        id=f"task_{task_number or state.next_task_number():03d}",
        source=source,
        agent=agent,
        kind=kind,
        priority=priority,
        capability=capability,
        required=required,
        analysis_required=analysis_required,
        fingerprint=fingerprint,
        action_id=action_id,
        depends_on=list(depends_on or []),
        project_ref=project_ref,
        input=dict(input or {}),
    )
    if append:
        state.tasks.append(task)
    return task
