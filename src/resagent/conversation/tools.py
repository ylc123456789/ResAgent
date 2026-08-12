"""Chat-layer tools — the action space of the conversation loop.

See docs/CONVERSATION_LAYER_DESIGN.md §4.5.

Tools (8):
    Tier 1 (no confirmation): consult_expert, list_runs, inspect_run,
                              list_sessions
    Tier 2 (user-confirmed):  propose_research_run, start_research_run,
                              advance_run, resume_subsession

Consults are advisory only: they NEVER create AgentTasks or mutate any
ResearchState. Their outputs land in ConversationState.recent_artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..capabilities import CapabilityRegistry
from .models import (
    ConversationEventType,
    ConversationState,
    ResearchBrief,
)
from ..config import Config
from .history import conversation_dir
from ..models import UserDirective
from ..orchestrator import init_run, resume_run, status as run_status
from ..persistence.state import save_state, submit_user_response


@dataclass
class ToolOutcome:
    """Result of one tool execution."""
    text: str                                         # fed back into the loop
    ok: bool = True
    state_patch: dict = field(default_factory=dict)   # applied to ConversationState
    extra_events: list[tuple[ConversationEventType, dict]] = field(default_factory=list)


class ChatTools:
    """Executes the 8 chat tools. Owns adapters and a controller factory."""

    def __init__(
        self,
        config: Config,
        registry: CapabilityRegistry,
        expagent,          # ExpAgentAdapter
        codingagent,       # CodingAgentAdapter
        controller_factory,  # callable() -> Controller
        reproagent=None,   # ReproAgentAdapter (needed for resume_subsession)
        mock: bool = False,
    ):
        self.config = config
        self.registry = registry
        self.expagent = expagent
        self.codingagent = codingagent
        self.reproagent = reproagent
        self.controller_factory = controller_factory
        self.mock = mock

    # ── dispatch ──────────────────────────────────────────────────────────

    def execute(self, conv: ConversationState, tool: str, params: dict) -> ToolOutcome:
        handlers = {
            "consult_expert": self._consult_expert,
            "list_runs": self._list_runs,
            "inspect_run": self._inspect_run,
            "propose_research_run": self._propose_research_run,
            "start_research_run": self._start_research_run,
            "advance_run": self._advance_run,
            "list_sessions": self._list_sessions,
            "resume_subsession": self._resume_subsession,
        }
        handler = handlers.get(tool)
        if handler is None:
            return ToolOutcome(
                ok=False,
                text=f"Unknown tool: {tool}. Available: {sorted(handlers)}",
            )
        try:
            return handler(conv, params or {})
        except Exception as e:
            return ToolOutcome(ok=False, text=f"Tool {tool} failed: {e}")

    # ── Tier 1: consult_expert ────────────────────────────────────────────

    def _consult_expert(self, conv: ConversationState, params: dict) -> ToolOutcome:
        expert = params.get("expert", "")
        instruction = params.get("instruction", "").strip()
        if not instruction:
            return ToolOutcome(ok=False, text="consult_expert requires non-empty 'instruction'.")

        allowed, reason = self.registry.check_callable(expert, tier=1)
        if not allowed:
            return ToolOutcome(ok=False, text=reason)

        consult_n = self._next_consult_number(conv, expert)
        out_dir = conversation_dir(conv.workspace_root, conv.conversation_id) \
            / "experts" / f"{expert}_{consult_n:03d}"

        if expert == "expagent":
            situation = instruction
            if conv.scratch_summary:
                situation += f"\n\nConversation background: {conv.scratch_summary}"
            artifacts = self._resolve_artifacts(conv, params.get("artifact_ids", []))
            raw = self.expagent.advise_adhoc(
                situation=situation,
                artifacts=artifacts,
                out_dir=str(out_dir),
                max_steps=self.config.chat.consult_max_steps,
                enable_paper_search=True,
            )
            text = self._render_decision(raw)
            ref = {
                "id": f"exp_consult_{consult_n:03d}",
                "type": "other",
                "path": str(out_dir / "scientific_decision.json"),
                "summary": str(raw.get("summary", ""))[:200],
                "source": "expagent",
            }

        elif expert == "codingagent_qa":
            workspace_path = params.get("workspace_path", "").strip()
            if not workspace_path:
                return ToolOutcome(
                    ok=False,
                    text="codingagent_qa requires 'workspace_path' (absolute repo path). "
                         "Ask the user for it via reply instead of guessing.",
                )
            raw = self.codingagent.ask_adhoc(
                question=instruction,
                workspace_path=workspace_path,
                out_dir=str(out_dir),
                max_steps=self.config.chat.consult_max_steps,
            )
            text = self._render_explanation(raw)
            ref = {
                "id": f"qa_consult_{consult_n:03d}",
                "type": "other",
                "path": str(out_dir / "code_explanation.json"),
                "summary": str(raw.get("answer", ""))[:200],
                "source": "codingagent_qa",
            }

        else:
            return ToolOutcome(
                ok=False,
                text=f"No chat consult path implemented for expert '{expert}'.",
            )

        return ToolOutcome(
            text=text + "\n\n[advisory only — no research task was created]",
            state_patch=self._consult_patch(ref, raw),
        )

    @staticmethod
    def _consult_patch(ref: dict, raw: dict) -> dict:
        """State patch for a consult: record the artifact + session index entry."""
        patch: dict = {"add_artifacts": [ref]}
        manifest = raw.get("_session_manifest")
        if manifest:
            from ..persistence.sessions import read_session_card, card_to_session_ref
            card = read_session_card(manifest)
            if card:
                patch["add_sessions"] = [card_to_session_ref(card, manifest)]
        return patch

    def _resolve_artifacts(self, conv: ConversationState, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        wanted = set(ids)
        return [a.model_dump() for a in conv.recent_artifacts if a.id in wanted]

    @staticmethod
    def _render_decision(raw: dict) -> str:
        parts = [f"ExpAgent advisory (confidence: {raw.get('confidence', '?')}):"]
        parts.append(str(raw.get("summary", "")))
        conclusion = raw.get("conclusion")
        if conclusion:
            parts.append(f"Conclusion: {conclusion.get('status', '?')} — "
                         f"{conclusion.get('rationale', '')[:300]}")
        actions = raw.get("recommended_actions") or []
        if actions:
            parts.append("Recommended actions (NOT executed, advisory only):")
            for a in actions[:5]:
                parts.append(f"  - [{a.get('type', '?')}] {a.get('rationale', '')[:120]}")
        risks = raw.get("risks") or []
        if risks:
            parts.append("Risks: " + "; ".join(str(r)[:100] for r in risks[:3]))
        return "\n".join(parts)

    @staticmethod
    def _render_explanation(raw: dict) -> str:
        parts = [f"CodingAgent QA (status: {raw.get('status', '?')}):"]
        parts.append(str(raw.get("answer", "")))
        evidence = raw.get("evidence_files") or []
        if evidence:
            parts.append("Evidence files: " + ", ".join(evidence[:10]))
        if raw.get("uncertainty"):
            parts.append(f"Uncertainty: {raw['uncertainty']}")
        return "\n".join(parts)

    @staticmethod
    def _next_consult_number(conv: ConversationState, expert: str) -> int:
        prefix = "exp_consult_" if expert == "expagent" else "qa_consult_"
        n = sum(1 for a in conv.recent_artifacts if a.id.startswith(prefix))
        return n + 1

    # ── Tier 1: run inspection ────────────────────────────────────────────

    def _list_runs(self, conv: ConversationState, params: dict) -> ToolOutcome:
        root = Path(conv.workspace_root)
        rows = []
        if root.exists():
            for d in root.iterdir():
                if not d.is_dir() or d.name == self.config.chat.conversations_dirname:
                    continue
                sp = d / "state.json"
                if not sp.exists():
                    continue
                try:
                    data = json.loads(sp.read_text(encoding="utf-8"))
                    run = data.get("run", {})
                    rows.append({
                        "run_id": run.get("run_id", d.name),
                        "status": run.get("status", "?"),
                        "goal": str(run.get("research_goal", ""))[:80],
                        "updated_at": str(run.get("updated_at", "")),
                    })
                except Exception:
                    continue
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        rows = rows[:20]
        if not rows:
            return ToolOutcome(text="No research runs found.")
        lines = [f"Found {len(rows)} run(s):"]
        for r in rows:
            lines.append(f"  - {r['run_id']} [{r['status']}] {r['goal']}")
        return ToolOutcome(text="\n".join(lines))

    def _inspect_run(self, conv: ConversationState, params: dict) -> ToolOutcome:
        run_id = params.get("run_id", "") or conv.active_run_id or ""
        if not run_id:
            return ToolOutcome(ok=False, text="inspect_run requires 'run_id' (no active run set).")
        text = run_status(conv.workspace_root, run_id)
        if text.startswith("No run found"):
            return ToolOutcome(ok=False, text=text)
        return ToolOutcome(
            text=text,
            state_patch={"active_run_id": run_id},
        )

    # ── sub-session index / resume ──────────────────────────────────────────

    def _list_sessions(self, conv: ConversationState, params: dict) -> ToolOutcome:
        from ..persistence.sessions import scan_session_cards

        run_id = params.get("run_id", "") or conv.active_run_id or ""
        if run_id:
            root = Path(conv.workspace_root) / run_id
        else:
            root = Path(conv.workspace_root)
        cards = scan_session_cards(root, limit=50)
        if not cards:
            scope = f"run {run_id}" if run_id else "workspace"
            return ToolOutcome(text=f"No sub-agent sessions found in {scope}.")
        lines = [f"Sub-agent sessions ({len(cards)}):"]
        for c in cards:
            lines.append(
                f"  - [{c.get('module', '?')}] {c.get('session_id', '?')} "
                f"[{c.get('status', '?')}] {str(c.get('summary', ''))[:60]}"
            )
            lines.append(f"      manifest: {c.get('_manifest_path', '')}")
        return ToolOutcome(text="\n".join(lines))

    def _resume_subsession(self, conv: ConversationState, params: dict) -> ToolOutcome:
        from ..persistence.sessions import read_session_card

        instruction = (params.get("instruction") or "").strip()
        if not instruction:
            return ToolOutcome(ok=False, text="resume_subsession requires non-empty 'instruction'.")

        manifest = (params.get("manifest_path") or "").strip()
        if not manifest:
            sid = (params.get("session_id") or "").strip()
            match = next((s for s in conv.session_index if s.session_id == sid), None)
            if match is None:
                return ToolOutcome(
                    ok=False,
                    text=f"Session '{sid}' not in the index. Use list_sessions to find it.",
                )
            manifest = match.manifest_path

        # Containment: only resume sessions inside this workspace
        try:
            Path(manifest).resolve().relative_to(Path(conv.workspace_root).resolve())
        except ValueError:
            return ToolOutcome(ok=False, text=f"Session path outside workspace: {manifest}")

        card = read_session_card(manifest)
        if card is None:
            return ToolOutcome(ok=False, text=f"Cannot read session card: {manifest}")
        module = card.get("module", "")
        project_path = card.get("project_path") or str(Path(manifest).parent)

        if module == "reproagent":
            if self.reproagent is None:
                return ToolOutcome(ok=False, text="ReproAgent adapter not wired.")
            result = self.reproagent.resume_session(
                project_path, instruction,
                max_steps=self.config.chat.default_advance_steps,
            )
        elif module == "codingagent":
            result = self.codingagent.resume_session(project_path, instruction)
        elif module == "expagent":
            return ToolOutcome(
                ok=False,
                text="Advisory sessions (expagent) are not resumable; start a new "
                     "consult_expert with the previous context quoted.",
            )
        else:
            return ToolOutcome(ok=False, text=f"Cannot resume module '{module}'.")

        status = result.get("status", "")
        summary = str(result.get("summary", ""))[:500]
        # Refresh the index entry from the card (sub-module rewrote it)
        from ..persistence.sessions import card_to_session_ref
        new_card = read_session_card(manifest) or card
        run_id = ""
        parent = new_card.get("parent") or {}
        if isinstance(parent, dict):
            run_id = parent.get("run_id", "")
        patch = {"add_sessions": [card_to_session_ref(new_card, manifest, run_id=run_id)]}

        return ToolOutcome(
            text=f"Session {new_card.get('session_id', '?')} resumed "
                 f"(module={module}, status={status}).\n{summary}",
            state_patch=patch,
        )

    # ── Tier 2: research run gate ─────────────────────────────────────────

    def _propose_research_run(self, conv: ConversationState, params: dict) -> ToolOutcome:
        raw_brief = params.get("brief")
        if not isinstance(raw_brief, dict):
            return ToolOutcome(ok=False, text="propose_research_run requires a 'brief' object.")
        try:
            brief = ResearchBrief.model_validate(raw_brief)
        except Exception as e:
            return ToolOutcome(ok=False, text=f"Invalid brief: {e}")
        if not brief.goal.strip():
            return ToolOutcome(ok=False, text="Brief goal must be non-empty.")

        # Archive the brief for audit
        briefs_dir = conversation_dir(conv.workspace_root, conv.conversation_id) / "briefs"
        briefs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        (briefs_dir / f"brief_{ts}.json").write_text(
            brief.model_dump_json(indent=2), encoding="utf-8"
        )

        return ToolOutcome(
            text=(
                "Research brief prepared. Present it to the user in full and ask "
                "for explicit confirmation. Do NOT call start_research_run until "
                "the user confirms.\n\n" + brief.render_display()
            ),
            extra_events=[(
                ConversationEventType.brief_proposed,
                {
                    "brief": brief.model_dump(),
                    "state_patch": {"pending_brief": brief.model_dump()},
                },
            )],
        )

    def _start_research_run(self, conv: ConversationState, params: dict) -> ToolOutcome:
        if conv.pending_brief is None:
            return ToolOutcome(
                ok=False,
                text="No pending brief. Call propose_research_run first and wait "
                     "for user confirmation.",
            )
        brief = conv.pending_brief
        max_steps = self._cap_steps(params.get("max_steps"))

        state = init_run(
            goal=brief.render_goal_text(),
            workspace_root=conv.workspace_root,
            config=self.config,
        )
        run_id = state.run.run_id

        controller = self.controller_factory()
        state = controller.run(state, max_steps=max_steps)
        save_state(state)

        summary = self._summarize_run_progress(state, max_steps)
        session_patch = self._scan_run_sessions(conv.workspace_root, run_id)
        return ToolOutcome(
            text=f"Research run created and advanced.\n{summary}",
            state_patch=session_patch,
            extra_events=[
                (ConversationEventType.brief_confirmed,
                 {"run_id": run_id, "state_patch": {"pending_brief": None}}),
                (ConversationEventType.run_created,
                 {"run_id": run_id,
                  "goal": brief.goal[:200],
                  "state_patch": {"active_run_id": run_id}}),
            ],
        )

    def _advance_run(self, conv: ConversationState, params: dict) -> ToolOutcome:
        run_id = params.get("run_id", "") or conv.active_run_id or ""
        instruction = params.get("instruction", "").strip()
        if not run_id:
            return ToolOutcome(ok=False, text="advance_run requires 'run_id' (no active run set).")
        if not instruction:
            return ToolOutcome(ok=False, text="advance_run requires non-empty 'instruction'.")

        state = resume_run(conv.workspace_root, run_id)
        if state is None:
            return ToolOutcome(ok=False, text=f"No run found: {conv.workspace_root}/{run_id}")

        if state.pending_question is not None:
            submit_user_response(state, state.pending_question.question_id, instruction)

        state.user_directives.append(UserDirective(
            text=instruction,
            source_conversation=conv.conversation_id,
        ))
        save_state(state)

        max_steps = self._cap_steps(params.get("max_steps"))
        controller = self.controller_factory()
        state = controller.run(state, max_steps=max_steps)
        save_state(state)

        summary = self._summarize_run_progress(state, max_steps)
        session_patch = self._scan_run_sessions(conv.workspace_root, run_id)
        session_patch["active_run_id"] = run_id
        return ToolOutcome(
            text=f"Run {run_id} advanced with your instruction.\n{summary}",
            state_patch=session_patch,
            extra_events=[(
                ConversationEventType.run_advanced,
                {"run_id": run_id,
                 "instruction": instruction[:500],
                 "state_patch": {"active_run_id": run_id}},
            )],
        )

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _scan_run_sessions(workspace_root: str, run_id: str) -> dict:
        """Scan a run dir for sub-agent session cards -> add_sessions patch."""
        from ..persistence.sessions import scan_session_cards, card_to_session_ref
        cards = scan_session_cards(Path(workspace_root) / run_id, limit=50)
        if not cards:
            return {}
        return {"add_sessions": [
            card_to_session_ref(c, c["_manifest_path"], run_id=run_id) for c in cards
        ]}

    def _cap_steps(self, requested) -> int:
        default = self.config.chat.default_advance_steps
        cap = self.config.chat.max_steps_per_turn
        try:
            steps = int(requested) if requested else default
        except (TypeError, ValueError):
            steps = default
        return max(1, min(steps, cap))

    @staticmethod
    def _summarize_run_progress(state, max_steps: int) -> str:
        lines = [
            f"  run_id: {state.run.run_id}",
            f"  status: {state.run.status.value}",
            f"  tasks: {len(state.tasks)} total, "
            f"{sum(1 for t in state.tasks if t.status.value == 'completed')} completed, "
            f"{sum(1 for t in state.tasks if t.status.value == 'failed')} failed, "
            f"{sum(1 for t in state.tasks if t.status.value == 'pending')} pending",
            f"  artifacts: {len(state.artifacts)}",
        ]
        recent = state.observations[-max_steps:]
        if recent:
            lines.append("  recent activity:")
            for o in recent:
                action = o.action.value if o.action else "?"
                lines.append(f"    - [{action}] {o.result}: {o.detail[:100]}")
        if state.run.status.value == "paused":
            lines.append("  (run is paused and waiting for user input)")
        return "\n".join(lines)
