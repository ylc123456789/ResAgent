"""Adapter for ReproAgent — the paper/repo reproduction module.

Uses ReproAgent's Python API (run_controller) directly. Falls back to mock.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from ..models import Artifact, ArtifactType, Producer, AgentTask
from ..context import build_reproagent_context
from ..workspace_layout import WorkspaceLayout


class ReproAgentAdapter:
    """Calls ReproAgent to reproduce paper results via its Python API."""

    def __init__(
        self,
        module_path: str = "",
        model: str = "deepseek-v4-pro",
        api_base: str = "https://api.deepseek.com",
        api_key_env: str = "DEEPSEEK_API_KEY",
        mock: bool = False,
    ):
        self.module_path = module_path
        self.model = model
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.mock = mock
        self._imported = False

    def execute(self, task: AgentTask, layout: WorkspaceLayout) -> dict:
        spec = build_reproagent_context(task)
        task_num = task.id.replace("task_", "")
        task_n = int(task_num) if task_num.isdigit() else 1

        # Task-level directory (ResAgent owns this)
        task_dir = layout.reproagent_task_dir(task_n)
        task_dir.mkdir(parents=True, exist_ok=True)
        # ReproAgent's actual workspace (nested inside task dir)
        repro_ws = layout.reproagent_workspace(task_n)

        layout.write_task_manifest(task_dir, task_id=task.id,
                                   module="ReproAgent",
                                   input_summary=task.input.get("experiment_goal", ""))

        if self.mock:
            raw = self._mock_execute(spec)
            returncode = 0
        else:
            raw, returncode = self._call_execute(spec, repro_ws)

        # Write adapter result WITHOUT overwriting ReproAgent's own state.json
        adapter_file = task_dir / layout.resagent_adapter_result()
        with open(adapter_file, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False, default=str)

        # Artifact path points to the actual result inside repo_workspace/
        actual_result = repro_ws / "result.md"
        if actual_result.exists():
            artifact_path = layout.relpath(actual_result)
        else:
            artifact_path = layout.relpath(repro_ws / "result.md")  # best-effort

        artifact = Artifact(
            id=f"repro_result_{task_num}",
            type=ArtifactType.repro_result,
            producer=Producer.ReproAgent,
            path=artifact_path,
            summary=raw.get("summary", task.input.get("experiment_goal", ""))[:200],
            metadata={"returncode": returncode, "raw_result": raw},
        )

        return {
            "artifact": artifact,
            "raw": raw,
            "stdout": raw.get("stdout", ""),
            "stderr": raw.get("stderr", ""),
            "returncode": returncode,
        }

    def _call_execute(self, spec: dict, out_dir: Path) -> tuple[dict, int]:
        self._ensure_import()
        from reproagent.models import ReproTask
        from reproagent.controller import run_controller

        task = ReproTask(
            paper_url=spec.get("paper_url", ""),
            repo_url=spec.get("repo_url", ""),
            workspace_dir=out_dir / "repo_workspace",
            experiment_goal=spec.get("experiment_goal", ""),
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
            timeout_seconds=spec.get("timeout") or 1800,
            mock_llm=False,
            enable_coding_agent=bool(spec.get("codingagent_path")),
            codingagent_path=Path(spec["codingagent_path"]) if spec.get("codingagent_path") else None,
            mirror_profile=spec.get("mirror_profile") or "none",
        )

        start = time.time()
        try:
            result_state = run_controller(task)
            returncode = 0 if result_state.status == "completed" else 1
            raw = {
                "status": result_state.status,
                "summary": result_state.final_summary or f"Reproduction completed in {time.time() - start:.0f}s",
                "steps": len(result_state.steps),
                "duration_seconds": round(time.time() - start, 1),
            }
            return raw, returncode
        except Exception as e:
            return {
                "status": "error",
                "summary": f"Reproduction failed: {e}",
                "duration_seconds": round(time.time() - start, 1),
                "error": str(e),
            }, 1

    def _mock_execute(self, spec: dict) -> dict:
        return {
            "status": "completed",
            "summary": f"Mock: reproduced {spec.get('experiment_goal', 'unknown')[:100]}",
            "steps": 5,
            "duration_seconds": 2.3,
            "returncode": 0,
            "results": {"test_accuracy": 0.9902},
        }

    def _ensure_import(self):
        if self._imported or self.mock:
            return
        paths = [self.module_path]
        src = os.path.join(self.module_path, "src") if self.module_path else ""
        if src and os.path.isdir(src):
            paths.insert(0, src)
        for p in paths:
            if p and p not in sys.path:
                sys.path.insert(0, p)
        try:
            import reproagent.controller  # noqa: F401
            import reproagent.models  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import ReproAgent. module_path={self.module_path}. "
                f"Set --reproagent-path or REPROAGENT_PATH. Error: {e}"
            )
        self._imported = True
