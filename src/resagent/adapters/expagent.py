"""Adapter for ExpAgent — the scientific advisor module.

Calls ExpAgent's advise() function with a properly constructed AdvisorContext,
then parses ScientificDecision back into ResAgent artifacts and tasks.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from ..models import (
    Artifact, ArtifactType, Producer, AgentTask, AgentKind, TaskPriority,
)
from ..task_contracts import (
    normalize_recommended_action,
    required_from_priority,
    task_fingerprint,
)
from ..workspace_layout import WorkspaceLayout


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

    def advise(self, state, layout) -> dict:
        """Call ExpAgent for scientific advice.

        Returns: {"artifact": Artifact, "tasks": list[AgentTask], "raw": dict}
        """
        dec_num = state.next_artifact_number()
        dec_dir = layout.expagent_decision_dir(dec_num)
        exp_run_dir = layout.expagent_run_dir(dec_num)

        layout.write_task_manifest(dec_dir, task_id=f"exp_decision_{dec_num:03d}",
                                   module="ExpAgent",
                                   input_summary=state.current_summary or state.run.research_goal[:200])

        if self.mock:
            raw = self._mock_advise(state)
            dec_dir.mkdir(parents=True, exist_ok=True)
            from ..session_cards import write_mock_card
            write_mock_card(dec_dir / "session.yaml", module="expagent",
                            session_id=f"exp-mock-{dec_num:03d}",
                            kind="advisory_session",
                            summary=raw.get("summary", "")[:100],
                            parent={"module": "resagent",
                                    "run_id": state.run.run_id,
                                    "task_id": f"exp_decision_{dec_num:03d}"})
        else:
            ctx = self._build_advisor_context(state)
            raw = self._call_advise(ctx, exp_run_dir)

        dec_dir.mkdir(parents=True, exist_ok=True)
        with open(dec_dir / "scientific_decision.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False, default=str)

        artifact_path = layout.relpath(dec_dir / "scientific_decision.json")
        metadata = {"raw_decision": raw}
        # ExpAgent writes its own card in its run dir (E1)
        # Ensure the card records the key artifact (ExpAgent may leave it empty)
        for cand in (exp_run_dir / "session.yaml", dec_dir / "session.yaml"):
            if cand.exists():
                metadata["session_manifest"] = str(cand)
                _patch_session_key_artifacts(cand, "scientific_decision",
                    "../scientific_decision.json",
                    raw.get("summary", "")[:120])
                break
        artifact = Artifact(
            id=f"exp_decision_{dec_num:03d}",
            type=ArtifactType.scientific_decision,
            producer=Producer.ExpAgent,
            path=artifact_path,
            summary=raw.get("summary", "")[:200],
            metadata=metadata,
        )

        self._state = state  # so inference helpers can access state
        tasks = self._actions_to_tasks(
            raw.get("recommended_actions", []),
            source=artifact.id,
            next_num=state.next_task_number(),
        )
        if self._normalization_issues:
            raw["_normalization_issues"] = self._normalization_issues

        return {"artifact": artifact, "tasks": tasks, "raw": raw}

    # ── ad-hoc advisory calls (conversation layer, no ResearchRun) ─────────

    def advise_adhoc(
        self,
        situation: str,
        artifacts: list[dict],
        out_dir: str,
        max_steps: int | None = None,
        enable_paper_search: bool = True,
    ) -> dict:
        """Advisory call outside any ResearchRun (chat layer Tier-1 consult).

        Returns the raw decision dict. Purely advisory: the caller
        (chat_tools) must NOT create AgentTasks from recommended_actions.
        """
        if self.mock:
            raw = self._mock_adhoc(situation)
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            from ..session_cards import write_mock_card
            write_mock_card(out / "session.yaml", module="expagent",
                            session_id="exp-mock-adhoc", kind="advisory_session",
                            summary=raw.get("summary", "")[:100])
            raw["_session_manifest"] = str(out / "session.yaml")
            return raw

        self._ensure_import()
        from experiment_designer.models import AdvisorContext, ArtifactRef
        from experiment_designer.advisor import advise

        refs = []
        for i, a in enumerate(artifacts):
            refs.append(ArtifactRef(
                id=a.get("id", f"ref_{i}"),
                type=_clamp_artifact_ref_type(a.get("type", "other")),
                path=a.get("path") or None,
                summary=a.get("summary", ""),
            ))

        ctx = AdvisorContext(situation=situation, artifacts=refs, existing_plan=None)

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        decision, extra = advise(
            ctx,
            model=self.model,
            api_base=self.api_base,
            api_key_env=self.api_key_env,
            mock=False,
            run_dir=out,
            max_steps=max_steps,
            enable_paper_search=enable_paper_search,
        )

        raw = decision.model_dump() if hasattr(decision, "model_dump") else {}
        if isinstance(raw, dict):
            raw["_extra"] = extra
        with open(out / "scientific_decision.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False, default=str)
        card = out / "session.yaml"
        if card.exists():
            raw["_session_manifest"] = str(card)
        return raw

    def _mock_adhoc(self, situation: str) -> dict:
        return {
            "summary": (
                f"Mock advisory on: {situation[:120]}... "
                "This is a deterministic mock answer for offline testing."
            ),
            "confidence": "medium",
            "conclusion": None,  # explanation-style response (E1 schema relaxation)
            "evidence": [],
            "experiment_plan": None,
            "recommended_actions": [],
            "risks": [],
            "needs_user_input": [],
        }

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

        # Link the advisory session card back to this run (ExpAgent E1 parent).
        parent_run = {
            "module": "resagent",
            "run_id": state.run.run_id,
            "task_id": f"exp_decision_{state.next_artifact_number():03d}",
        }
        ctx_kwargs = dict(situation=situation, artifacts=artifacts, existing_plan=None)
        if "parent_run" in AdvisorContext.model_fields:
            ctx_kwargs["parent_run"] = parent_run
        return AdvisorContext(**ctx_kwargs)

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
            rp = t.input.get("workspace_path") or t.input.get("repo_path", "")
            if rp:
                repo_paths.add(rp)
        if repo_paths:
            parts.append(f"Available Workspaces: {', '.join(sorted(repo_paths))}")

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

    def _call_advise(self, ctx, run_dir: Path) -> dict:
        self._ensure_import()
        from experiment_designer.advisor import advise

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
                        "workspace_path": "./",
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
        """Convert ExpAgent recommended_actions into ResAgent AgentTasks.

        ExpAgent fills scientific fields (kind, task_goal, rationale, etc.).
        ResAgent normalizes executor/capability and fills operational fields.
        """
        self._normalization_issues: list[str] = []
        tasks = []

        for action in actions:
            action_type = action.get("type", "ask_user")
            raw_pri = action.get("priority", "medium")
            if isinstance(raw_pri, str):
                priority = TaskPriority(raw_pri) if raw_pri in ("high", "medium", "low") else TaskPriority.medium
            elif isinstance(raw_pri, (int, float)):
                priority = TaskPriority.high if raw_pri <= 1 else TaskPriority.medium
            else:
                priority = TaskPriority.medium

            try:
                agent, kind, capability, plan = normalize_recommended_action(
                    action, self._state,
                )
            except ValueError as exc:
                self._normalization_issues.append(str(exc))
                continue

            task_input = {
                "description": action.get("rationale", ""),
                "action_type": action_type,
                "task_goal": plan.get("task_goal", ""),
                "paper_url": plan.get("paper_url", ""),
                "repo_url": plan.get("repo_url", ""),
                "experiment_goal": plan.get("experiment_goal", ""),
                "expected_metrics": plan.get("expected_metrics", []),
                "command_goal": plan.get("command_goal", ""),
                "search_query": plan.get("search_query", ""),
                "question": plan.get("question", ""),
                "supersedes_task_id": plan.get("supersedes_task_id", ""),
                "workspace_path": _infer_workspace_path(
                    self._state, plan, action,
                ),
                "constraints": _infer_constraints(plan),
                "verify_commands": _infer_verify_commands(plan),
                "expected_artifacts": plan.get("expected_artifacts", []),
                "requires_gpu": plan.get("requires_gpu", False),
                "expected_runtime": plan.get("expected_runtime", ""),
            }
            fingerprint = task_fingerprint(agent, capability, task_input)
            if (self._state.find_task_by_fingerprint(fingerprint) is not None
                    or any(t.fingerprint == fingerprint for t in tasks)):
                continue

            task = AgentTask(
                id=f"task_{next_num + len(tasks):03d}",
                source=source,
                agent=agent,
                kind=kind,
                priority=priority,
                capability=capability,
                required=required_from_priority(priority, action),
                fingerprint=fingerprint,
                input=task_input,
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


def _clamp_artifact_ref_type(t: str) -> str:
    """Clamp to ExpAgent ArtifactRef's accepted type vocabulary."""
    allowed = {"repro_result", "code_patch", "run_log", "metric_summary", "other"}
    return t if t in allowed else "other"


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


# ── Parameter inference helpers ───────────────────────────────────────────────
# These fill operational fields that ExpAgent leaves empty.
# ExpAgent's job is scientific decisions; ResAgent's job is operational params.


def _infer_workspace_path(state, plan: dict, action: dict) -> str:
    """Infer the workspace path for a coding task.

    Priority:
    1. ExpAgent-provided value (respect scientific judgment)
    2. Extract from research goal text
    3. Inherit from existing tasks
    4. Empty string (LLM Planner or user fills in)
    """
    # 1. ExpAgent already filled it (new name or old compat name)
    for key in ("workspace_path", "repo_path"):
        val = plan.get(key, "")
        if val and val.strip():
            return val.strip()

    # 2. Try extracting from research goal
    goal = state.run.research_goal if state else ""
    for pattern in [r'(/[^\s,;]+)', r'([A-Za-z]:\\[^\s,;]+)']:
        match = re.search(pattern, goal)
        if match:
            path = match.group(1).rstrip(".")
            # Skip URLs and bare hostnames (e.g. "//github.com/pytorch")
            if "://" in path or path.startswith("//"):
                continue
            # If it looks like a file path, use parent directory
            if "." in os.path.basename(path) and not os.path.isdir(path):
                parent = os.path.dirname(path)
                if parent:
                    return parent
            return path

    # 3. Inherit from existing tasks
    if state:
        for t in state.tasks:
            for key in ("workspace_path", "repo_path"):
                p = t.input.get(key, "")
                if p:
                    return p

    return ""


def _infer_constraints(plan: dict) -> list[str]:
    """Generate default constraints if ExpAgent didn't provide them."""
    existing = plan.get("constraints", [])
    if existing:
        return list(existing) if isinstance(existing, list) else [existing]

    kind = plan.get("kind", "")
    defaults = {
        "coding_task": [
            "Do not change training semantics or model architecture",
            "Only modify files necessary for the stated goal",
        ],
    }
    return defaults.get(kind, [])


def _infer_verify_commands(plan: dict) -> list[str]:
    """Generate default verify commands if ExpAgent didn't provide them."""
    existing = plan.get("verify_commands", [])
    if existing:
        return list(existing) if isinstance(existing, list) else [existing]

    kind = plan.get("kind", "")
    if kind == "coding_task":
        return ["python -m py_compile *.py"]
    return []


def _patch_session_key_artifacts(card_path: Path, artifact_type: str,
                                 artifact_relpath: str, summary: str) -> None:
    """Ensure a session.yaml includes a key artifact entry.

    ExpAgent may write the card with key_artifacts empty; ResAgent patches
    it after the fact so the card is self-contained for discovery.
    """
    try:
        import yaml
        data = yaml.safe_load(card_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return
    if not isinstance(data, dict):
        return
    existing = data.get("key_artifacts") or []
    paths = {a.get("path", "") for a in existing if isinstance(a, dict)}
    if artifact_relpath not in paths:
        existing.append({
            "type": artifact_type,
            "path": artifact_relpath,
            "summary": summary,
        })
        data["key_artifacts"] = existing
        try:
            card_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False),
                                 encoding="utf-8")
        except OSError:
            pass
