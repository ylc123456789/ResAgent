"""Sub-agent session discovery and resume tools for the conversation layer."""

from __future__ import annotations

from pathlib import Path

from ..persistence.sessions import (
    card_to_session_ref,
    read_session_card,
    scan_session_cards,
)
from .models import ConversationState


class SessionToolsMixin:
    """Session-oriented handlers mixed into ChatTools."""

    def _list_sessions(self, conv: ConversationState, params: dict):
        run_id = params.get("run_id", "") or conv.active_run_id or ""
        root = Path(conv.workspace_root) / run_id if run_id else Path(conv.workspace_root)
        cards = scan_session_cards(root, limit=50)
        if not cards:
            scope = f"run {run_id}" if run_id else "workspace"
            return self._outcome(text=f"No sub-agent sessions found in {scope}.")
        lines = [f"Sub-agent sessions ({len(cards)}):"]
        for card in cards:
            lines.append(
                f"  - [{card.get('module', '?')}] {card.get('session_id', '?')} "
                f"[{card.get('status', '?')}] {str(card.get('summary', ''))[:60]}"
            )
            lines.append(f"      manifest: {card.get('_manifest_path', '')}")
        return self._outcome(text="\n".join(lines))

    def _resume_subsession(self, conv: ConversationState, params: dict):
        instruction = (params.get("instruction") or "").strip()
        if not instruction:
            return self._outcome(
                ok=False, text="resume_subsession requires non-empty 'instruction'."
            )

        manifest = (params.get("manifest_path") or "").strip()
        if not manifest:
            session_id = (params.get("session_id") or "").strip()
            match = next(
                (item for item in conv.session_index if item.session_id == session_id),
                None,
            )
            if match is None:
                return self._outcome(
                    ok=False,
                    text=f"Session '{session_id}' not in the index. Use list_sessions to find it.",
                )
            manifest = match.manifest_path

        try:
            Path(manifest).resolve().relative_to(Path(conv.workspace_root).resolve())
        except ValueError:
            return self._outcome(
                ok=False, text=f"Session path outside workspace: {manifest}"
            )

        card = read_session_card(manifest)
        if card is None:
            return self._outcome(ok=False, text=f"Cannot read session card: {manifest}")
        module = card.get("module", "")
        project_path = card.get("project_path") or str(Path(manifest).parent)

        if module == "reproagent":
            if self.reproagent is None:
                return self._outcome(ok=False, text="ReproAgent adapter not wired.")
            result = self.reproagent.resume_session(
                project_path,
                instruction,
                max_steps=self.config.chat.default_advance_steps,
            )
        elif module == "codingagent":
            result = self.codingagent.resume_session(project_path, instruction)
        elif module == "expagent":
            return self._outcome(
                ok=False,
                text=(
                    "Advisory sessions (expagent) are not resumable; start a new "
                    "consult_expert with the previous context quoted."
                ),
            )
        else:
            return self._outcome(ok=False, text=f"Cannot resume module '{module}'.")

        status = result.get("status", "")
        summary = str(result.get("summary", ""))[:500]
        new_card = read_session_card(manifest) or card
        parent = new_card.get("parent") or {}
        run_id = parent.get("run_id", "") if isinstance(parent, dict) else ""
        patch = {
            "add_sessions": [card_to_session_ref(new_card, manifest, run_id=run_id)]
        }
        return self._outcome(
            text=(
                f"Session {new_card.get('session_id', '?')} resumed "
                f"(module={module}, status={status}).\n{summary}"
            ),
            state_patch=patch,
        )

    @staticmethod
    def _scan_run_sessions(workspace_root: str, run_id: str) -> dict:
        """Scan a run directory and return a conversation session patch."""
        cards = scan_session_cards(Path(workspace_root) / run_id, limit=50)
        if not cards:
            return {}
        return {
            "add_sessions": [
                card_to_session_ref(card, card["_manifest_path"], run_id=run_id)
                for card in cards
            ]
        }
