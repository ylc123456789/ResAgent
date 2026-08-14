"""Report generation — execution plan, summary, artifact index."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import ResearchState


def generate_all(state: ResearchState) -> None:
    """Generate all reports for a research run."""
    ws = Path(state.run.workspace_dir) / state.run.run_id
    ws.mkdir(parents=True, exist_ok=True)

    _write_execution_plan(state, ws)
    _write_summary(state, ws)
    _write_artifact_index(state, ws)


def _write_execution_plan(state: ResearchState, ws: Path) -> None:
    lines = [
        f"# Execution Plan: {state.run.run_id}",
        "",
        f"**Goal**: {state.run.research_goal}",
        f"**Status**: {state.run.status.value}",
        f"**Updated**: {state.run.updated_at.isoformat()}",
        "",
        "## Tasks",
        "",
    ]

    for t in state.tasks:
        lines.append(f"### {t.id} [{t.status.value}] {t.priority.value}")
        lines.append(f"- Agent: {t.agent.value} / {t.kind.value}")
        lines.append(f"- Source: {t.source}")
        if t.input.get("description"):
            lines.append(f"- Description: {t.input['description']}")
        if t.error:
            lines.append(f"- Error: {t.error}")
        for warning in t.warnings:
            lines.append(f"- Warning: {warning}")
        lines.append("")

    lines += [
        "## Recent Decisions",
        "",
    ]
    for d in state.decisions[-10:]:
        lines.append(f"- [{d.id}] {d.made_by}: {d.reason[:200]}")
    lines.append("")

    (ws / "execution_plan.md").write_text("\n".join(lines), encoding="utf-8")


def _write_summary(state: ResearchState, ws: Path) -> None:
    lines = [
        f"# Summary: {state.run.run_id}",
        "",
        f"**Goal**: {state.run.research_goal}",
        f"**Status**: {state.run.status.value}",
        "",
        "## Current State",
        "",
        state.current_summary or "(no summary yet)",
        "",
    ]

    # Key results
    completed_tasks = [t for t in state.tasks if t.status.value == "completed"]
    if completed_tasks:
        lines += ["## Completed Tasks", ""]
        for t in completed_tasks:
            lines.append(f"- {t.id}: {t.input.get('description', t.kind.value)}")

    failed_tasks = [t for t in state.tasks if t.status.value == "failed"]
    if failed_tasks:
        lines += ["", "## Failed Tasks", ""]
        for t in failed_tasks:
            lines.append(f"- {t.id}: {t.error[:200]}")

    (ws / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _write_artifact_index(state: ResearchState, ws: Path) -> None:
    artifacts_dir = ws / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    index = {
        "run_id": state.run.run_id,
        "artifacts": [
            {
                "id": a.id,
                "type": a.type.value,
                "producer": a.producer.value,
                "path": a.path,
                "summary": a.summary,
                "created_at": a.created_at.isoformat(),
            }
            for a in state.artifacts
        ],
    }

    (artifacts_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
