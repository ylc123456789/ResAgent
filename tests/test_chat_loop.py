"""End-to-end chat loop tests with scripted LLM responses (no API calls)."""

import json
from pathlib import Path

import pytest

from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter
from resagent.capabilities import CapabilityRegistry
from resagent.chat import ChatLoop, handle_slash
from resagent.chat_models import ConversationEventType
from resagent.chat_tools import ChatTools
from resagent.config import Config
from resagent.conversation import (
    load_conversation,
    new_conversation,
    read_events,
    rebuild_from_events,
)
from resagent.orchestrator import build_controller, init_run
from resagent.state import load_state


def tc(tool, params=None, reason=""):
    return json.dumps({"type": "tool_call", "tool": tool,
                       "params": params or {}, "reason": reason},
                      ensure_ascii=False)


def rp(text):
    return json.dumps({"type": "reply", "text": text}, ensure_ascii=False)


@pytest.fixture
def stack(tmp_path):
    cfg = Config()
    registry = CapabilityRegistry(cfg)
    registry.load()
    ctrl = build_controller(cfg, mock=True)
    tools = ChatTools(
        cfg, registry,
        expagent=ExpAgentAdapter(mock=True),
        codingagent=CodingAgentAdapter(mock=True),
        controller_factory=lambda: ctrl,
        reproagent=ReproAgentAdapter(mock=True),
        mock=True,
    )
    conv = new_conversation(str(tmp_path))
    return cfg, registry, tools, conv


def _chat(cfg, registry, tools, scripted):
    return ChatLoop(cfg, registry, tools, scripted_responses=scripted)


def _run_dirs(workspace_root: str) -> list[Path]:
    return [d for d in Path(workspace_root).iterdir()
            if d.is_dir() and d.name != "conversations"]


def _event_types(conv):
    return [e.type for e in read_events(conv.workspace_root, conv.conversation_id)]


# ── routing behavior ──────────────────────────────────────────────────────────

def test_chat_qa_routes_to_expert(stack):
    cfg, registry, tools, conv = stack
    chat = _chat(cfg, registry, tools, [
        tc("consult_expert", {"expert": "expagent",
                              "instruction": "用户问：layer norm 为什么有效？"}),
        rp("LayerNorm 通过归一化稳定训练……"),
    ])
    reply = chat.handle_message(conv, "layer norm 为什么有效？")
    assert "LayerNorm" in reply

    types = _event_types(conv)
    assert types == [
        ConversationEventType.user_message,
        ConversationEventType.tool_call,
        ConversationEventType.tool_result,
        ConversationEventType.assistant_message,
    ]
    assert conv.recent_artifacts[0].source == "expagent"
    # QA must NOT create a research run
    assert _run_dirs(conv.workspace_root) == []


def test_chat_idea_discussion_no_run(stack):
    cfg, registry, tools, conv = stack
    chat = _chat(cfg, registry, tools, [
        tc("consult_expert", {"expert": "expagent",
                              "instruction": "用户想讨论：diffusion 用于时序异常检测"}),
        rp("这个方向有戏，主要风险是……如果你想推进，我可以帮你立项。"),
    ])
    chat.handle_message(conv, "diffusion 用到时序异常检测有戏吗？")
    assert _run_dirs(conv.workspace_root) == []
    assert conv.pending_brief is None


def test_chat_explicit_start_creates_run(stack):
    cfg, registry, tools, conv = stack
    chat = _chat(cfg, registry, tools, [
        tc("propose_research_run", {"brief": {
            "goal": "验证 diffusion 时序异常检测",
            "context_summary": "前几轮讨论了可行性",
        }}),
        rp("研究提案已准备好，请确认是否立项。"),
    ])
    reply = chat.handle_message(conv, "就按刚才讨论的方向开始做实验")
    assert "确认" in reply
    assert _run_dirs(conv.workspace_root) == []  # nothing before confirmation

    chat2 = _chat(cfg, registry, tools, [
        tc("start_research_run", {}),
        rp("已创建 run 并完成首轮咨询。"),
    ])
    chat2.handle_message(conv, "确认")

    assert conv.active_run_id is not None
    assert conv.pending_brief is None
    run_dirs = _run_dirs(conv.workspace_root)
    assert len(run_dirs) == 1
    state = load_state(conv.workspace_root, conv.active_run_id)
    assert state is not None
    assert "验证 diffusion 时序异常检测" in state.run.research_goal
    # mock controller ran steps: artifacts produced
    assert len(state.observations) >= 1


def test_chat_propose_requires_confirm(stack):
    cfg, registry, tools, conv = stack
    chat = _chat(cfg, registry, tools, [
        tc("propose_research_run", {"brief": {"goal": "g"}}),
        rp("请确认。"),
    ])
    chat.handle_message(conv, "开始做实验")
    # user never confirms; conversation ends
    assert conv.pending_brief is not None
    assert _run_dirs(conv.workspace_root) == []


def test_chat_mixed_intent(stack):
    """'Explain X, and if it makes sense plan an experiment' — consult first,
    mention run option in the reply, but do NOT create a run unprompted."""
    cfg, registry, tools, conv = stack
    chat = _chat(cfg, registry, tools, [
        tc("consult_expert", {"expert": "expagent",
                              "instruction": "用户问：channel attention 为什么可能有效？"}),
        rp("机制上它显式建模通道依赖……如果你觉得有前途，我可以帮你立项验证。"),
    ])
    reply = chat.handle_message(
        conv, "channel attention 为什么有效？有前途的话帮我规划个实验")
    assert "立项" in reply or "规划" in reply
    assert conv.recent_artifacts[0].source == "expagent"
    assert _run_dirs(conv.workspace_root) == []


def test_chat_tool_budget(stack):
    cfg, registry, tools, conv = stack
    cfg.chat.max_tool_calls_per_turn = 2
    chat = _chat(cfg, registry, tools, [tc("list_runs")] * 3)
    reply = chat.handle_message(conv, "loop please")
    assert "中间进展" in reply
    # budget+1 iterations worth of tool calls, then a forced final reply
    assert _event_types(conv)[-1] == ConversationEventType.assistant_message


def test_chat_advance_injects_directive(stack):
    cfg, registry, tools, conv = stack
    state = init_run(goal="Goal", workspace_root=conv.workspace_root, config=cfg)
    run_id = state.run.run_id

    chat = _chat(cfg, registry, tools, [
        tc("advance_run", {"run_id": run_id, "instruction": "失败了的话换个 baseline 继续"}),
        rp("已推进。"),
    ])
    chat.handle_message(conv, "上次那个实验，失败了的话换个 baseline 继续")

    state = load_state(conv.workspace_root, run_id)
    assert state.user_directives[0].text == "失败了的话换个 baseline 继续"


def test_chat_resume(stack):
    cfg, registry, tools, conv = stack
    chat = _chat(cfg, registry, tools, [
        tc("inspect_run", {}),
        rp("当前没有活动 run。"),
    ])
    # make inspect_run fail-soft: no run_id given -> error result, still a reply
    chat.handle_message(conv, "看看状态")

    reloaded = load_conversation(conv.workspace_root, conv.conversation_id)
    assert reloaded is not None
    assert reloaded.event_count == conv.event_count

    rebuilt = rebuild_from_events(conv.workspace_root, conv.conversation_id)
    assert rebuilt.event_count == conv.event_count


# ── slash commands (deterministic, no LLM) ────────────────────────────────────

def test_slash_commands(stack):
    cfg, registry, tools, conv = stack

    assert "Slash commands" in handle_slash(conv, "/help", tools)
    assert "No research runs" in handle_slash(conv, "/runs", tools)
    assert "No pending brief" in handle_slash(conv, "/confirm", tools)
    assert "Unknown command" in handle_slash(conv, "/bogus", tools)
    assert handle_slash(conv, "not a slash command", tools) is None
    # absolute paths and multi-token inputs must pass through to the LLM
    assert handle_slash(conv, "/home/cyl/ResAgent 入口在哪个文件？", tools) is None
    assert handle_slash(conv, "/tmp/repo", tools) is None

    # deterministic propose -> /brief -> /confirm flow
    out = tools.execute(conv, "propose_research_run", {"brief": {"goal": "验证 X"}})
    conv.apply_patch(out.extra_events[0][1]["state_patch"])
    assert "验证 X" in handle_slash(conv, "/brief", tools)

    result = handle_slash(conv, "/confirm", tools)
    assert "run_id" in result
    assert conv.active_run_id is not None
    assert conv.pending_brief is None

    # /status uses the active run
    assert conv.active_run_id in handle_slash(conv, "/status", tools)

    # /sessions lists sub-agent sessions of the active run
    sessions_out = handle_slash(conv, "/sessions", tools)
    assert "Sub-agent sessions" in sessions_out or "No sub-agent sessions" in sessions_out


def test_slash_new_and_quit(stack):
    from resagent.chat import _NEW, _QUIT
    _, _, tools, conv = stack
    assert handle_slash(conv, "/new", tools) == _NEW
    assert handle_slash(conv, "/quit", tools) == _QUIT
