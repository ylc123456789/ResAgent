"""Conversation agentic loop + REPL — the unified user entry point.

See docs/CONVERSATION_LAYER_DESIGN.md §4.6.

Routing philosophy: there is NO intent classifier stage. The chat loop IS
the router — the LLM chooses tools (or a direct reply) with full
conversation context, one JSON decision per turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .capabilities import CapabilityRegistry
from .chat_models import ConversationEventType, ConversationState
from .chat_tools import ChatTools
from .config import Config
from .conversation import (
    append_event,
    new_conversation,
    read_events,
)
from .llm import call_chat
from .planner import _extract_json
from .prompts import CHAT_SYSTEM

RECENT_EVENT_WINDOW = 12
OLDER_EVENT_WINDOW = 28  # deterministic fold of older events (no LLM compression in v1)


@dataclass
class ChatMessage:
    """One parsed LLM response: either a tool call or a final reply."""
    kind: str            # "tool_call" | "reply"
    tool: str = ""
    params: dict = None
    reason: str = ""
    text: str = ""


class ChatLoop:
    """One conversation turn = up to N tool calls + one final reply."""

    def __init__(
        self,
        config: Config,
        registry: CapabilityRegistry,
        tools: ChatTools,
        mock: bool = False,
        scripted_responses: list[str] | None = None,
    ):
        self.config = config
        self.registry = registry
        self.tools = tools
        self.mock = mock
        self.dirname = config.chat.conversations_dirname
        # Test hook: pop scripted LLM responses in order (takes precedence
        # over keyword mock and real API).
        self.scripted = list(scripted_responses) if scripted_responses else None

    def _append(self, conv, etype, payload):
        return append_event(conv, etype, payload, self.dirname)

    # ── main entry ────────────────────────────────────────────────────────

    def handle_message(self, conv: ConversationState, text: str) -> str:
        """Process one user message; return the text shown to the user."""
        self._append(conv, ConversationEventType.user_message, {"text": text})

        budget = self.config.chat.max_tool_calls_per_turn
        for _ in range(budget + 1):
            system, user = self._build_prompt(conv)
            raw = self._respond(conv, system, user, text)
            try:
                msg = self._parse(raw)
            except Exception as e:
                self._append(conv, ConversationEventType.error,
                             {"error": f"parse error: {e}", "raw": raw[:500]})
                return self._finalize(conv, "（系统未能理解内部响应，请换个说法重试。）")

            if msg.kind == "reply":
                return self._finalize(conv, msg.text)

            outcome = self.tools.execute(conv, msg.tool, msg.params)
            self._append(conv, ConversationEventType.tool_call, {
                "tool": msg.tool, "params": msg.params, "reason": msg.reason,
            })
            self._append(conv, ConversationEventType.tool_result, {
                "tool": msg.tool,
                "ok": outcome.ok,
                "detail": outcome.text[:2000],
                "state_patch": outcome.state_patch,
            })
            for etype, payload in outcome.extra_events:
                self._append(conv, etype, payload)

        # Tool budget exhausted — force a reply with what we have.
        return self._finalize(
            conv,
            "（本轮处理步骤较多，先汇报中间进展；如需继续请直接说。）",
        )

    def _finalize(self, conv: ConversationState, text: str) -> str:
        self._append(conv, ConversationEventType.assistant_message, {"text": text})
        return text

    # ── prompt building ───────────────────────────────────────────────────

    def _build_prompt(self, conv: ConversationState) -> tuple[str, str]:
        system = CHAT_SYSTEM.replace("{experts}", self.registry.router_descriptions())

        parts = ["## Conversation Snapshot"]
        parts.append(f"- active_run_id: {conv.active_run_id or '(none)'}")
        if conv.pending_brief is not None:
            parts.append("- pending_brief: AWAITING USER CONFIRMATION\n"
                         + conv.pending_brief.render_display())
        else:
            parts.append("- pending_brief: (none)")
        if conv.recent_artifacts:
            parts.append("- recent artifacts:")
            for a in conv.recent_artifacts[-10:]:
                parts.append(f"    [{a.id}] ({a.source}) {a.summary[:80]}")

        events = read_events(conv.workspace_root, conv.conversation_id, self.dirname)
        older, recent = events[:-RECENT_EVENT_WINDOW], events[-RECENT_EVENT_WINDOW:]

        if conv.scratch_summary:
            parts.append(f"\n## Earlier Summary\n{conv.scratch_summary}")
        if older:
            parts.append("\n## Older Events (compressed)")
            for e in older[-OLDER_EVENT_WINDOW:]:
                parts.append(self._event_line(e))
        parts.append("\n## Recent Events")
        for e in recent:
            parts.append(self._event_line(e))
        parts.append("\nDecide the next step now. Remember: exactly one JSON object.")

        return system, "\n".join(parts)

    @staticmethod
    def _event_line(e) -> str:
        p = e.payload
        if e.type == ConversationEventType.user_message:
            return f"U: {str(p.get('text', ''))[:150]}"
        if e.type == ConversationEventType.assistant_message:
            return f"A: {str(p.get('text', ''))[:150]}"
        if e.type == ConversationEventType.tool_call:
            return f"-> {p.get('tool', '?')}: {str(p.get('reason', ''))[:80]}"
        if e.type == ConversationEventType.tool_result:
            mark = "ok" if p.get("ok") else "ERR"
            return f"<- {p.get('tool', '?')} [{mark}]: {str(p.get('detail', ''))[:120]}"
        return f"[{e.type.value}]"

    # ── LLM response handling ─────────────────────────────────────────────

    def _respond(self, conv: ConversationState, system: str, user: str,
                 raw_text: str) -> str:
        if self.scripted is not None:
            if not self.scripted:
                raise RuntimeError("scripted_responses exhausted")
            return self.scripted.pop(0)
        if self.mock:
            return self._mock_respond(conv, raw_text)
        return call_chat(
            system, user,
            model=self.config.llm.model,
            api_base=self.config.llm.api_base,
            api_key_env=self.config.llm.api_key_env,
        )

    @staticmethod
    def _parse(raw: str) -> ChatMessage:
        data = _extract_json(raw)
        if data.get("type") == "tool_call":
            return ChatMessage(
                kind="tool_call",
                tool=data.get("tool", ""),
                params=data.get("params", {}) or {},
                reason=data.get("reason", ""),
            )
        return ChatMessage(kind="reply", text=data.get("text", "") or str(data))

    # ── deterministic mock (offline demo / smoke tests) ───────────────────

    def _mock_respond(self, conv: ConversationState, text: str) -> str:
        """Keyword-rule mock so `resagent chat --mock` demos the full flow."""
        import json

        def call(tool, params, reason=""):
            return json.dumps({"type": "tool_call", "tool": tool,
                               "params": params, "reason": reason},
                              ensure_ascii=False)

        def reply(t):
            return json.dumps({"type": "reply", "text": t}, ensure_ascii=False)

        # A tool just finished this turn -> wrap up with a reply.
        events = read_events(conv.workspace_root, conv.conversation_id, self.dirname)
        last = events[-1] if events else None
        if last is not None and last.type in (
            ConversationEventType.tool_result,
            ConversationEventType.brief_rejected,
            ConversationEventType.run_created,
            ConversationEventType.run_advanced,
        ):
            if last.type == ConversationEventType.run_created:
                return reply(
                    f"已创建并启动 run {conv.active_run_id}。"
                    "可用 /status 查看进展，或直接告诉我下一步指令。"
                )
            # find the preceding tool_result for the detail text
            detail, ok = "", True
            for e in reversed(events):
                if e.type == ConversationEventType.tool_result:
                    detail = str(e.payload.get("detail", ""))
                    ok = bool(e.payload.get("ok", True))
                    break
            if ok:
                return reply(detail[:1500] if detail else "已完成。")
            return reply(f"出错了：{detail[:500]}")
        if last is not None and last.type == ConversationEventType.brief_proposed:
            if conv.pending_brief is not None:
                return reply(
                    "研究提案已准备好：\n" + conv.pending_brief.render_display()
                    + "\n\n请确认是否立项（回复「确认」或 /confirm）。"
                )
            return reply("提案已处理。")

        # Awaiting brief confirmation?
        if conv.pending_brief is not None:
            if re.search(r"确认|好的?|可以|开始|同意|yes|ok|confirm", text, re.I):
                return call("start_research_run", {}, "User confirmed the brief.")
            if re.search(r"取消|算了|不要|no|cancel", text, re.I):
                return reply("好的，已放弃该研究提案。")
            return reply("研究提案仍在等待确认。请回复「确认」开始，或「取消」放弃。")

        # Explicit run start?
        if re.search(r"立项|开始实验|开始做|启动|做实验|规划实验|开始做实验", text):
            return call("propose_research_run", {
                "brief": {"goal": text.strip(),
                          "context_summary": conv.scratch_summary[:300]},
            }, "User explicitly wants to start a research project.")

        # Progress / status question about existing run?
        if re.search(r"状态|进度|跑得|怎么样了|结果", text) and (
                conv.active_run_id or re.search(r"res-\d", text)):
            run_id = conv.active_run_id or re.search(r"res-[\w-]+", text).group(0)
            return call("inspect_run", {"run_id": run_id}, "User asks about run status.")

        # Code question? (needs a path)
        if re.search(r"代码|repo|仓库|loss|训练入口|报错|哪行|入口在哪", text) \
                and re.search(r"[?？吗]|哪|怎么|什么|如何", text):
            m = re.search(r"(/[^\s,;.?？]+)", text)
            if m:
                return call("consult_expert", {
                    "expert": "codingagent_qa",
                    "instruction": f"用户问：{text.strip()}",
                    "workspace_path": m.group(1),
                }, "Code question with a repo path.")
            return reply("这是一个代码问题。请提供目标 repo 的路径，我再请代码专家只读分析。")

        # Scientific question / idea discussion?
        if re.search(r"[?？]|为什么|怎么|什么|有戏|靠谱|觉得|idea", text, re.I):
            return call("consult_expert", {
                "expert": "expagent",
                "instruction": f"用户问：{text.strip()}\n（对话咨询，按需检索，不必强行产出实验计划。）",
            }, "Scientific question / idea discussion.")

        return reply(
            "我可以帮你：咨询科学问题、讨论研究想法、查看/推进已有实验、"
            "或把成熟想法立项为研究项目。你想做什么？"
        )


# ── REPL ────────────────────────────────────────────────────────────────────

_QUIT = "__QUIT__"
_NEW = "__NEW__"

KNOWN_SLASH = {
    "/help", "/runs", "/status", "/use", "/brief",
    "/confirm", "/cancel", "/new", "/quit", "/exit", "/q",
}


def handle_slash(conv: ConversationState, cmd: str, tools: ChatTools) -> str | None:
    """Handle a slash command locally (never reaches the LLM).

    Returns the text to display, a sentinel (_QUIT/_NEW), or None if the
    input is not a slash command. Inputs that merely START with '/' but are
    not known commands (e.g. absolute paths like /home/user/repo ...) pass
    through to the chat loop.
    """
    if not cmd.startswith("/"):
        return None
    parts = cmd.strip().split(maxsplit=1)
    name, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    if name not in KNOWN_SLASH:
        # Multi-token or path-like input: not a command, let the LLM see it.
        if arg or cmd.count("/") > 1:
            return None
        return f"Unknown command: {name}. Try /help."

    if name in ("/quit", "/exit", "/q"):
        return _QUIT
    if name == "/new":
        return _NEW
    if name == "/help":
        return (
            "Slash commands:\n"
            "  /runs              list research runs\n"
            "  /status [run_id]   show run status (default: active run)\n"
            "  /use <run_id>      set the active run\n"
            "  /brief             show the pending research brief\n"
            "  /confirm           confirm the pending brief and start the run\n"
            "  /cancel            discard the pending brief\n"
            "  /new               start a new conversation\n"
            "  /quit              exit"
        )
    if name == "/runs":
        return tools.execute(conv, "list_runs", {}).text
    if name == "/status":
        return tools.execute(conv, "inspect_run", {"run_id": arg}).text
    if name == "/use":
        if not arg:
            return "Usage: /use <run_id>"
        return tools.execute(conv, "inspect_run", {"run_id": arg}).text
    if name == "/brief":
        if conv.pending_brief is None:
            return "No pending brief."
        return "Pending brief (awaiting confirmation):\n" + conv.pending_brief.render_display()
    dirname = tools.config.chat.conversations_dirname
    if name == "/cancel":
        if conv.pending_brief is None:
            return "No pending brief."
        append_event(conv, ConversationEventType.brief_rejected,
                     {"state_patch": {"pending_brief": None}}, dirname)
        return "Brief discarded."
    if name == "/confirm":
        if conv.pending_brief is None:
            return "No pending brief to confirm."
        outcome = tools.execute(conv, "start_research_run", {})
        append_event(conv, ConversationEventType.tool_result, {
            "tool": "start_research_run", "ok": outcome.ok,
            "detail": outcome.text[:2000], "state_patch": outcome.state_patch,
        }, dirname)
        for etype, payload in outcome.extra_events:
            append_event(conv, etype, payload, dirname)
        return outcome.text
    return f"Unknown command: {name}. Try /help."  # unreachable guard


def run_repl(conv: ConversationState, chat: ChatLoop, tools: ChatTools,
             workspace_root: str) -> None:
    """Interactive chat REPL."""
    print(f"ResAgent chat — conversation {conv.conversation_id}")
    print("Type your message, /help for commands, /quit to exit.")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        result = handle_slash(conv, text, tools)
        if result == _QUIT:
            break
        if result == _NEW:
            conv = new_conversation(workspace_root,
                                    chat.config.chat.conversations_dirname)
            print(f"New conversation: {conv.conversation_id}")
            continue
        if result is not None:
            print(f"\nresagent> {result}")
            continue
        reply = chat.handle_message(conv, text)
        print(f"\nresagent> {reply}")
    print("Bye.")
