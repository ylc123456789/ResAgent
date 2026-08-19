"""Adapter for CodingAgent — the repo-scoped coding module."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..models import Artifact, ArtifactType, Producer, AgentTask
from ..context import build_codingagent_context
from ..persistence.workspace import WorkspaceLayout


class CodingAgentAdapter:
    """Calls CodingAgent for well-defined code tasks."""

    def __init__(
        self,
        module_path: str = "",
        model: str = "deepseek-v4-pro",
        api_base: str = "https://api.deepseek.com",
        api_key_env: str = "DEEPSEEK_API_KEY",
        max_steps: int = 48,
        resource_root: str = "",
        mock: bool = False,
    ):
        self.module_path = module_path
        self.model = model
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.max_steps = max_steps
        self.resource_root = resource_root
        self.mock = mock
        self._imported = False

    def execute(self, task: AgentTask, layout: WorkspaceLayout, attempt_number: int = 1) -> dict:
        """Execute a coding task via CodingAgent. Returns dict with artifact, raw, outcome."""
        spec = build_codingagent_context(task)
        task_num = task.id.replace("task_", "")
        task_n = int(task_num) if task_num.isdigit() else 1

        out_dir = layout.codingagent_attempt_dir(task_n, attempt_number)
        out_dir.mkdir(parents=True, exist_ok=True)

        layout.write_task_manifest(out_dir, task_id=task.id,
                                   module="CodingAgent", attempt=attempt_number,
                                   input_summary=task.input.get("task_goal", ""))

        if self.mock:
            raw = self._mock_execute(spec)
            (out_dir / "patch_report.md").write_text(
                f"# Mock CodingAgent Report\n\n{raw['summary']}\n",
                encoding="utf-8",
            )
            from ..persistence.sessions import write_mock_card
            write_mock_card(out_dir / "session.yaml", module="codingagent",
                            session_id=f"code-mock-{task_num}",
                            summary=raw.get("summary", "")[:100],
                            parent={"module": "resagent",
                                    "run_id": layout.run_id,
                                    "task_id": task.id,
                                    "attempt": attempt_number})
        else:
            raw = self._call_execute(
                spec, out_dir,
                parent_run={"module": "resagent",
                            "run_id": layout.run_id,
                            "task_id": task.id,
                            "attempt": attempt_number},
            )

        # Write adapter result WITHOUT overwriting CodingAgent's own state.json
        adapter_file = out_dir / layout.resagent_adapter_result()
        with open(adapter_file, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False, default=str)

        artifact_path = layout.relpath(out_dir / "patch_report.md")
        metadata = {"raw_result": raw}
        card = out_dir / "session.yaml"
        if card.exists():
            metadata["session_manifest"] = str(card)
        artifact = Artifact(
            id=f"code_patch_{task_num}",
            type=ArtifactType.code_patch,
            producer=Producer.CodingAgent,
            path=artifact_path,
            summary=raw.get("summary", task.input.get("task_goal", ""))[:200],
            metadata=metadata,
        )

        outcome = raw.get("status", "completed")
        if outcome not in {"completed", "completed_with_warnings", "blocked", "needs_user_input", "failed"}:
            outcome = "completed" if raw.get("verification_passed", True) else "failed"
        return {
            "artifact": artifact,
            "raw": raw,
            "outcome": outcome,
            "workspace_path": spec.get("workspace_path", ""),
            "session_manifest": str(card) if card.exists() else "",
        }

    # ── ad-hoc read-only QA (conversation layer, no ResearchRun) ───────────

    def ask_adhoc(
        self,
        question: str,
        workspace_path: str,
        out_dir: str,
        context_hint: str = "",
        max_steps: int | None = None,
    ) -> dict:
        """Read-only code question (chat layer Tier-1 consult).

        Returns the raw CodeExplanation dict.
        """
        if self.mock:
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            from ..persistence.sessions import write_mock_card
            write_mock_card(out / "session.yaml", module="codingagent",
                            session_id="code-qa-mock", kind="qa_session",
                            summary=f"Mock QA: {question[:80]}")
            return {
                "status": "completed",
                "answer": (
                    f"Mock answer to: {question[:120]}... "
                    "This is a deterministic mock for offline testing."
                ),
                "evidence_files": [],
                "relevant_snippets": [],
                "uncertainty": "mock mode",
                "commands_run": [],
                "_session_manifest": str(out / "session.yaml"),
            }

        self._ensure_import()
        from coding_agent import CodeQuestionSpec, run_code_question

        ws = Path(workspace_path).expanduser()
        if not ws.exists():
            raise RuntimeError(f"workspace_path does not exist: {ws}")

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        spec = CodeQuestionSpec(
            workspace_path=ws,
            question=question,
            output_dir=out,
            context_hint=context_hint,
            max_steps=max_steps or 12,
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
        )
        result = run_code_question(spec)

        raw = result.model_dump() if hasattr(result, "model_dump") else {}
        with open(out / "code_explanation.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False, default=str)
        card = out / "session.yaml"
        if card.exists():
            raw["_session_manifest"] = str(card)
        return raw

    # ── session resume (conversation layer) ────────────────────────────────

    def resume_session(self, output_dir: str, instruction: str) -> dict:
        """Resume a code task session in-place (same output_dir, same session_id).

        instruction: the user's new directive, quoted verbatim.
        """
        if self.mock:
            return {
                "status": "completed",
                "summary": f"Mock resume: {instruction[:100]}",
                "changed_files": [],
                "residual_risks": [],
            }

        self._ensure_import()
        from coding_agent import resume_code_task

        result = resume_code_task(
            Path(output_dir),
            instruction,
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
        )
        return result.model_dump() if hasattr(result, "model_dump") else {"summary": str(result)}

    def _call_execute(self, spec: dict, out_dir: Path,
                      parent_run: dict | None = None) -> dict:
        self._ensure_import()
        from coding_agent import CodeTaskSpec, run_code_task

        workspace_path = spec.get("workspace_path", "")
        repo_url = spec.get("repo_url", "")
        # Cloning (repo_url set) with no prior workspace: clone into a fresh
        # subdir of the attempt dir so CodingAgent's "workspace must be empty"
        # guard passes instead of falling back to the process cwd.
        if repo_url and not workspace_path:
            workspace_path = str(out_dir / "repo")

        task_spec = CodeTaskSpec(
            workspace_path=Path(workspace_path),
            repo_url=repo_url,
            branch=spec.get("branch", ""),
            env_policy=spec.get("env_policy", "auto"),
            env_name=spec.get("env_name", ""),
            requires_gpu=bool(spec.get("requires_gpu", False)),
            task_goal=spec.get("task_goal", ""),
            constraints=spec.get("constraints", []),
            verify_commands=spec.get("verify_commands", []),
            allowed_paths=spec.get("allowed_paths", []),
            max_steps=self.max_steps,
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
            parent_run=parent_run,
            output_dir=out_dir,
            # M2: content-addressed env management (no-op when empty).
            resource_root=self.resource_root,
            project_ref=spec.get("project_ref", ""),
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
