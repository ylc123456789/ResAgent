"""Adapter for CodingAgent — the repo-scoped coding module."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..models import Artifact, ArtifactType, Producer, AgentTask
from ..context import build_codingagent_context


class CodingAgentAdapter:
    """Calls CodingAgent for well-defined code tasks."""

    def __init__(
        self,
        module_path: str = "",
        model: str = "deepseek-v4-pro",
        api_base: str = "https://api.deepseek.com",
        api_key_env: str = "DEEPSEEK_API_KEY",
        max_steps: int = 48,
        mock: bool = False,
    ):
        self.module_path = module_path
        self.model = model
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.max_steps = max_steps
        self.mock = mock
        self._imported = False

    def execute(self, task: AgentTask, workspace_dir: str) -> dict:
        spec = build_codingagent_context(task)
        task_num = task.id.replace("task_", "")

        out_dir = Path(workspace_dir) / f"codingagent/code_{task_num}"
        out_dir.mkdir(parents=True, exist_ok=True)

        if self.mock:
            raw = self._mock_execute(spec)
        else:
            raw = self._call_execute(spec, out_dir)

        artifact = Artifact(
            id=f"code_patch_{task_num}",
            type=ArtifactType.code_patch,
            producer=Producer.CodingAgent,
            path=f"codingagent/code_{task_num}/patch_report.md",
            summary=raw.get("summary", task.input.get("task_goal", ""))[:200],
            metadata={"raw_result": raw},
        )

        with open(out_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False, default=str)

        return {"artifact": artifact, "raw": raw}

    def _call_execute(self, spec: dict, out_dir: Path) -> dict:
        self._ensure_import()
        from coding_agent import CodeTaskSpec, run_code_task

        task_spec = CodeTaskSpec(
            repo_path=spec.get("repo_path", ""),
            task_goal=spec.get("task_goal", ""),
            constraints=spec.get("constraints", []),
            verify_commands=spec.get("verify_commands", []),
            allowed_paths=spec.get("allowed_paths", []),
            max_steps=self.max_steps,
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
            output_dir=str(out_dir),
        )

        result = run_code_task(task_spec)

        if hasattr(result, "model_dump"):
            return result.model_dump()
        if isinstance(result, dict):
            return result
        return {"summary": str(result), "patches_applied": 0}

    def _mock_execute(self, spec: dict) -> dict:
        return {
            "summary": f"Mock: completed {spec.get('task_goal', 'unknown')[:100]}",
            "files_changed": 2,
            "patches_applied": 1,
            "verification_passed": True,
            "steps": 3,
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
            import coding_agent  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import CodingAgent. module_path={self.module_path}. "
                f"Set --codingagent-path or CODINGAGENT_PATH. Error: {e}"
            )
        self._imported = True
