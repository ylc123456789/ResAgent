"""Adapter for ExpAgent — the scientific advisor module.

Calls ExpAgent's advise() function with a properly constructed AdvisorContext,
then parses ScientificDecision back into ResAgent artifacts and tasks.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..models import (
    Artifact, ArtifactType, Producer, AgentTask, AgentKind, TaskPriority,
)


class ExpAgentAdapter:
    """Calls ExpAgent and converts its output into ResAgent artifacts + tasks."""

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

    def advise(self, state, workspace_dir: str) -> dict:
        """Call ExpAgent for scientific advice.

        Returns: {"artifact": Artifact, "tasks": list[AgentTask], "raw": dict}
        """
        if self.mock:
            raw = self._mock_advise(state)
        else:
            ctx = self._build_advisor_context(state)
            raw = self._call_advise(ctx, workspace_dir)

        dec_num = state.next_artifact_number()
        artifact = Artifact(
            id=f"exp_decision_{dec_num:03d}",
            type=ArtifactType.scientific_decision,
            producer=Producer.ExpAgent,
            path=f"expagent/decision_{dec_num:03d}/scientific_decision.json",
            summary=raw.get("summary", "")[:200],
            metadata={"raw_decision": raw},
        )

        dec_dir = Path(workspace_dir) / f"expagent/decision_{dec_num:03d}"
        dec_dir.mkdir(parents=True, exist_ok=True)
        with open(dec_dir / "scientific_decision.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False, default=str)

        tasks = self._actions_to_tasks(
            raw.get("recommended_actions", []),
            source=artifact.id,
            next_num=state.next_task_number(),
        )

        return {"artifact": artifact, "tasks": tasks, "raw": raw}

    # ── context building ─────────────────────────────────────────────────

    def _build_advisor_context(self, state) -> "AdvisorContext":
        """Build a real ExpAgent AdvisorContext from ResAgent state."""
        self._ensure_import()

        from experiment_designer.models import AdvisorContext, ArtifactRef

        situation = self._build_situation(state)

        artifacts = []
        for a in state.artifacts[-20:]:
            artifacts.append(ArtifactRef(
                id=a.id,
                type=_map_artifact_type(a.type.value),
                path=a.path,
                summary=a.summary,
            ))

        return AdvisorContext(
            situation=situation,
            artifacts=artifacts,
            existing_plan=None,
        )

    def _build_situation(self, state) -> str:
        """Build a rich situation string for ExpAgent.

        Includes research goal, current state, available repos/paths,
        and recent results.
        """
        parts = [f"Research Goal: {state.run.research_goal}"]

        if state.current_summary:
            parts.append(f"Current Summary: {state.current_summary}")

        repo_paths = set()
        for t in state.tasks:
            rp = t.input.get("repo_path", "")
            if rp:
                repo_paths.add(rp)
        if repo_paths:
            parts.append(f"Available Repositories: {', '.join(sorted(repo_paths))}")

        if state.tasks:
            task_lines = ["Current Tasks:"]
            for t in state.tasks[-10:]:
                desc = t.input.get("description", "") or t.input.get("task_goal", "")
                task_lines.append(
                    f"  [{t.status.value}] {t.id} ({t.agent.value}/{t.kind.value})"
                    f"{': ' + desc[:120] if desc else ''}"
                )
            parts.append("\n".join(task_lines))

        return "\n\n".join(parts)

    # ── real call ─────────────────────────────────────────────────────────

    def _call_advise(self, ctx, workspace_dir: str) -> dict:
        self._ensure_import()
        from experiment_designer.advisor import advise

        run_dir = Path(workspace_dir) / "expagent"
        run_dir.mkdir(parents=True, exist_ok=True)

        decision, extra = advise(
            ctx,
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
            mock=False,
            run_dir=run_dir,
        )

        # Serialize ScientificDecision to dict
        raw = decision.model_dump() if hasattr(decision, "model_dump") else {}
        if isinstance(raw, dict):
            raw["_extra"] = extra
        return raw

    # ── mock ──────────────────────────────────────────────────────────────

    def _mock_advise(self, state) -> dict:
        situation = state.current_summary or state.run.research_goal[:200]
        return {
            "summary": f"Mock analysis of research goal. Suggests baseline first.",
            "confidence": "medium",
            "conclusion": {
                "status": "needs_more_experiments",
                "rationale": "Mock: need to establish baselines before comparisons.",
            },
            "evidence": [],
            "experiment_plan": None,
            "recommended_actions": [
                {
                    "priority": 1,
                    "type": "repro_task",
                    "rationale": "Reproduce a known MNIST baseline for comparison.",
                    "plan": {
                        "kind": "repro_task",
                        "paper_url": "https://arxiv.org/abs/example",
                        "repo_url": "https://github.com/example/mnist-baseline",
                        "experiment_goal": "Reproduce baseline CNN MNIST accuracy",
                    },
                },
                {
                    "priority": 2,
                    "type": "coding_task",
                    "rationale": "Add consistent metric logging to the training code.",
                    "plan": {
                        "kind": "coding_task",
                        "repo_path": "./",
                        "task_goal": "Add parameter count and FLOPs logging",
                        "constraints": ["Do not change training semantics"],
                        "verify_commands": ["python train.py --epochs 1 --dry-run"],
                        "expected_artifacts": ["logs/metrics.json"],
                    },
                },
            ],
            "risks": [],
            "needs_user_input": False,
        }

    # ── task conversion ───────────────────────────────────────────────────

    def _actions_to_tasks(
        self, actions: list[dict], source: str, next_num: int
    ) -> list[AgentTask]:
        """Convert ExpAgent recommended_actions into ResAgent AgentTasks."""
        tasks = []
        kind_map = {
            "coding_task": (AgentKind.coding_task, Producer.CodingAgent),
            "repro_task": (AgentKind.repro_task, Producer.ReproAgent),
            "run_task": (AgentKind.repro_task, Producer.ReproAgent),
            "literature_search": (AgentKind.advise, Producer.ExpAgent),
            "ask_user": (AgentKind.ask_user, Producer.ResAgent),
        }

        for i, action in enumerate(actions):
            action_type = action.get("type", "ask_user")
            plan = action.get("plan", {})

            kind, agent = kind_map.get(action_type, (AgentKind.advise, Producer.ExpAgent))
            raw_pri = action.get("priority", "medium")
            if isinstance(raw_pri, str):
                priority = TaskPriority(raw_pri) if raw_pri in ("high", "medium", "low") else TaskPriority.medium
            elif isinstance(raw_pri, (int, float)):
                priority = TaskPriority.high if raw_pri <= 1 else TaskPriority.medium
            else:
                priority = TaskPriority.medium

            task = AgentTask(
                id=f"task_{next_num + i:03d}",
                source=source,
                agent=agent,
                kind=kind,
                priority=priority,
                input={
                    "description": action.get("rationale", ""),
                    "action_type": action_type,
                    "repo_path": plan.get("repo_path", ""),
                    "task_goal": plan.get("task_goal", ""),
                    "constraints": plan.get("constraints", []),
                    "verify_commands": plan.get("verify_commands", []),
                    "expected_artifacts": plan.get("expected_artifacts", []),
                    "paper_url": plan.get("paper_url", ""),
                    "repo_url": plan.get("repo_url", ""),
                    "experiment_goal": plan.get("experiment_goal", ""),
                    "command_goal": plan.get("command_goal", ""),
                    "search_query": plan.get("search_query", ""),
                    "question": plan.get("question", ""),
                },
            )
            tasks.append(task)

        return tasks

    def _ensure_import(self):
        if self._imported or self.mock:
            return
        # Try module_path, and also module_path/src (common project layout)
        paths = [self.module_path]
        src = os.path.join(self.module_path, "src")
        if os.path.isdir(src):
            paths.insert(0, src)
        for p in paths:
            if p and p not in sys.path:
                sys.path.insert(0, p)
        try:
            import experiment_designer.advisor  # noqa: F401
            import experiment_designer.models  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import ExpAgent. module_path={self.module_path}. "
                f"Set --expagent-path or EXPAGENT_PATH. Error: {e}"
            )
        self._imported = True


def _map_artifact_type(t: str) -> str:
    """Map ResAgent ArtifactType values to ExpAgent's accepted type strings."""
    mapping = {
        "code_patch": "code_patch",
        "repro_result": "repro_result",
        "log": "run_log",
        "report": "other",
        "scientific_decision": "other",
        "experiment_plan": "other",
    }
    return mapping.get(t, "other")
