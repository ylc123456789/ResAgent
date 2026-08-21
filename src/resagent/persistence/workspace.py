"""Workspace layout helper — single source of truth for all run/task paths.

See docs/reference/ARTIFACT_AND_WORKSPACE_MANAGEMENT.md §4.2 for the target layout.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class WorkspaceLayout:
    """Central authority for directory paths within a research run.

    Every adapter MUST use this class to determine where to write files.
    No adapter should hardcode path strings like "codingagent/code_NNN".
    """

    def __init__(self, workspace_root: str, run_id: str):
        self.root = Path(workspace_root)
        self.run_id = run_id
        self.run_dir = self.root / run_id
        self.tasks_dir = self.run_dir / "tasks"

    # -- top-level run files --

    @property
    def state_json(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def execution_plan_md(self) -> Path:
        return self.run_dir / "execution_plan.md"

    @property
    def summary_md(self) -> Path:
        return self.run_dir / "summary.md"

    @property
    def artifacts_dir(self) -> Path:
        return self.run_dir / "artifacts"

    @property
    def artifacts_index(self) -> Path:
        return self.artifacts_dir / "index.json"

    def project_workspace(self, project_ref: str = "") -> Path:
        """Return the ResAgent-owned workspace for a logical project."""
        safe = "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in project_ref.strip()
        ).strip("_")
        return self.run_dir / "project_ws" / (safe or "default")

    # -- per-module task directories --

    def expagent_decision_dir(self, n: int) -> Path:
        """Directory owned by ResAgent for one ExpAgent decision.

        Contains scientific_decision.json + task_manifest.json.
        ExpAgent's own run_dir is a subdirectory `run/`.
        """
        return self.tasks_dir / "expagent" / f"decision_{n:03d}"

    def expagent_run_dir(self, n: int) -> Path:
        """ExpAgent's own run_dir — passed to advise() as run_dir parameter."""
        return self.expagent_decision_dir(n) / "run"

    def codingagent_task_dir(self, n: int) -> Path:
        """Directory for one CodingAgent task — passed as output_dir."""
        return self.tasks_dir / "codingagent" / f"task_{n:03d}"

    def codingagent_attempt_dir(self, n: int, attempt: int) -> Path:
        """Directory owned by one CodingAgent execution attempt."""
        return self.codingagent_task_dir(n) / f"attempt_{attempt:03d}"

    def reproagent_task_dir(self, n: int) -> Path:
        """Directory for one ReproAgent task."""
        return self.tasks_dir / "reproagent" / f"task_{n:03d}"

    def reproagent_attempt_dir(self, n: int, attempt: int) -> Path:
        """Directory owned by one ReproAgent execution attempt."""
        return self.reproagent_task_dir(n) / f"attempt_{attempt:03d}"

    def reproagent_workspace(self, n: int, attempt: int = 1) -> Path:
        """ReproAgent workspace passed to run_controller()."""
        return self.reproagent_attempt_dir(n, attempt) / "repo_workspace"

    # -- adapter-owned files within task directories --

    @staticmethod
    def resagent_adapter_result() -> str:
        return "resagent_adapter_result.json"

    @staticmethod
    def task_manifest_filename() -> str:
        return "task_manifest.json"

    # -- relative paths for artifact registration --

    def relpath(self, full_path: Path) -> str:
        """Return path relative to run_dir, for artifact registration."""
        return str(full_path.relative_to(self.run_dir))

    # -- task manifest --

    def write_task_manifest(self, task_dir: Path, *, task_id: str,
                            module: str, attempt: int = 1,
                            input_summary: str = "", capability: str = "",
                            project_ref: str = "",
                            depends_on: list[str] | None = None,
                            input_artifacts: list[str] | None = None) -> Path:
        """Write task_manifest.json into the task directory."""
        task_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "task_id": task_id,
            "module": module,
            "attempt": attempt,
            "input_summary": input_summary[:500],
            "capability": capability,
            "project_ref": project_ref,
            "depends_on": list(depends_on or []),
            "input_artifacts": list(input_artifacts or []),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
        }
        path = task_dir / self.task_manifest_filename()
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        return path

    # -- artifact validation --

    @staticmethod
    def validate_artifact_path(path: str, run_dir: Path) -> bool:
        """Check that a registered artifact path actually exists."""
        return (run_dir / path).exists()
