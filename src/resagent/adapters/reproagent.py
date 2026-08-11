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
        mirror_profile: str = "none",
        dataset_cache_dir: str = "",
        mock: bool = False,
    ):
        self.module_path = module_path
        self.model = model
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.mirror_profile = mirror_profile
        self.dataset_cache_dir = dataset_cache_dir
        self.mock = mock
        self._imported = False

    def execute(self, task: AgentTask, layout: WorkspaceLayout, attempt_number: int = 1) -> dict:
        """Execute a reproduction task via ReproAgent. Returns dict with artifact, outcome, raw."""
        spec = build_reproagent_context(task)
        task_num = task.id.replace("task_", "")
        task_n = int(task_num) if task_num.isdigit() else 1

        # Task-level directory (ResAgent owns this)
        task_dir = layout.reproagent_attempt_dir(task_n, attempt_number)
        task_dir.mkdir(parents=True, exist_ok=True)
        # ReproAgent's actual workspace (nested inside task dir)
        repro_ws = layout.reproagent_workspace(task_n, attempt_number)

        layout.write_task_manifest(task_dir, task_id=task.id,
                                   module="ReproAgent", attempt=attempt_number,
                                   input_summary=task.input.get("experiment_goal", ""))

        if self.mock:
            raw = self._mock_execute(spec)
            outcome = "completed"
            from ..session_cards import write_mock_card
            write_mock_card(repro_ws / "session.yaml", module="reproagent",
                            session_id=f"repro-mock-{task_num}",
                            summary=raw.get("summary", "")[:100])
        else:
            raw, outcome = self._call_execute(
                spec, repro_ws,
                parent_run={"module": "resagent", "run_id": layout.run_id,
                            "task_id": task.id},
            )

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

        metadata = {"outcome": outcome, "raw_result": raw}
        card = repro_ws / "session.yaml"
        if card.exists():
            metadata["session_manifest"] = str(card)
        artifact = Artifact(
            id=f"repro_result_{task_num}",
            type=ArtifactType.repro_result,
            producer=Producer.ReproAgent,
            path=artifact_path,
            summary=raw.get("summary", task.input.get("experiment_goal", ""))[:200],
            metadata=metadata,
        )

        return {
            "artifact": artifact,
            "outcome": outcome,
            "raw": raw,
        }

    def _call_execute(self, spec: dict, out_dir: Path,
                      parent_run: dict | None = None) -> tuple[dict, str]:
        self._ensure_import()
        from reproagent.models import ReproTask
        from reproagent.controller import run_controller

        task = ReproTask(
            paper_url=spec.get("paper_url", ""),
            repo_url=spec.get("repo_url", ""),
            workspace_dir=out_dir,
            experiment_goal=spec.get("experiment_goal", ""),
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
            timeout_seconds=spec.get("timeout") or 1800,
            mock_llm=False,
            enable_coding_agent=bool(spec.get("codingagent_path")),
            codingagent_path=Path(spec["codingagent_path"]) if spec.get("codingagent_path") else None,
            mirror_profile=spec.get("mirror_profile") or self.mirror_profile,
            # Dataset cache: per-task value (if any) wins; otherwise the
            # system-wide default from config/env. Without this, ReproAgent's
            # cache mechanism is silently disabled under orchestration.
            dataset_cache_dir=spec.get("dataset_cache_dir") or self.dataset_cache_dir,
            # Per-project conda env (shared across tasks of the same run)
            # and the parent pointer on the session card.
            env_namespace=parent_run.get("run_id", "") if parent_run else "",
            parent_run=parent_run,
        )

        start = time.time()
        try:
            result_state = run_controller(task)
            status = result_state.status
            if status == "completed":
                outcome = "completed"
            elif status == "completed_with_failures":
                outcome = "completed_with_warnings"
            elif status == "blocked":
                outcome = "blocked"
            elif status == "needs_user_input":
                outcome = "needs_user_input"
            else:
                outcome = "failed"
            raw = {
                "status": status,
                "outcome": outcome,
                "summary": result_state.final_summary or f"Reproduction finished in {time.time() - start:.0f}s",
                "steps": len(result_state.steps),
                "duration_seconds": round(time.time() - start, 1),
            }
            return raw, outcome
        except Exception as e:
            return {
                "status": "error",
                "summary": f"Reproduction failed: {e}",
                "duration_seconds": round(time.time() - start, 1),
                "error": str(e),
            }, "failed"

    # ── session resume (conversation layer) ────────────────────────────────

    def resume_session(self, workspace_dir: str, instruction: str,
                       max_steps: int | None = None) -> dict:
        """Resume a reproduction session in-place.

        Same workspace, same task_id (conda env reused), previous result
        summary + the new instruction injected as the goal.
        """
        if self.mock:
            return {
                "status": "completed",
                "summary": f"Mock resume: {instruction[:100]}",
                "steps": 2,
            }

        self._ensure_import()
        from reproagent.controller import run_controller
        from reproagent.models import AgentState, ReproTask

        ws = Path(workspace_dir)
        state_path = ws / "state.json"
        if not state_path.exists():
            raise RuntimeError(f"No state.json in {ws} — cannot resume.")

        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        orig_task = state_data.get("task", {})
        prev_summary = state_data.get("final_summary", "")

        goal = (
            "## Continuation of previous task\n\n"
            f"New instruction: {instruction}\n\n"
            f"Original goal: {orig_task.get('experiment_goal', '')}\n\n"
            f"Previous results (summary): {prev_summary[:2000]}"
        )
        payload = {
            **orig_task,
            "workspace_dir": str(ws),
            "experiment_goal": goal,
            "mock_llm": False,
            "model": self.model,
            "api_base": self.api_base,
            "api_key_env": self.api_key_env,
        }
        if max_steps:
            payload["max_steps"] = max_steps
        if not payload.get("dataset_cache_dir") and self.dataset_cache_dir:
            payload["dataset_cache_dir"] = self.dataset_cache_dir

        task = ReproTask.model_validate(payload)  # same task_id → env reused
        old_state = AgentState.model_validate(state_data)
        result_state = run_controller(task, resume_state=old_state)
        return {
            "status": result_state.status,
            "summary": result_state.final_summary,
            "steps": len(result_state.steps),
            "session_manifest": str(ws / "session.yaml"),
        }

    def _mock_execute(self, spec: dict) -> dict:
        return {
            "status": "completed",
            "outcome": "completed",
            "summary": f"Mock: reproduced {spec.get('experiment_goal', 'unknown')[:100]}",
            "steps": 5,
            "duration_seconds": 2.3,
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
