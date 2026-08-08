"""Tests for the 5 chat tools (mock adapters, no API calls)."""

import json
from pathlib import Path

import pytest

from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.capabilities import CapabilityRegistry
from resagent.chat_models import ConversationEventType
from resagent.chat_tools import ChatTools
from resagent.config import Config
from resagent.context import build_controller_context
from resagent.conversation import new_conversation
from resagent.orchestrator import build_controller, init_run
from resagent.state import load_state


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
        mock=True,
    )
    conv = new_conversation(str(tmp_path))
    return cfg, registry, tools, conv


def _run_dirs(workspace_root: str) -> list[Path]:
    return [d for d in Path(workspace_root).iterdir()
            if d.is_dir() and d.name != "conversations"]


# ── consult_expert ────────────────────────────────────────────────────────────

def test_consult_expagent_advisory(stack):
    _, _, tools, conv = stack
    out = tools.execute(conv, "consult_expert", {
        "expert": "expagent",
        "instruction": "用户问：layer norm 为什么有效？",
    })
    assert out.ok
    assert "advisory only" in out.text
    assert out.state_patch["add_artifacts"][0]["source"] == "expagent"
    # advisory must not create any run
    assert _run_dirs(conv.workspace_root) == []


def test_consult_codingagent_qa(stack, tmp_path):
    _, _, tools, conv = stack
    repo = tmp_path / "some_repo"
    repo.mkdir()
    out = tools.execute(conv, "consult_expert", {
        "expert": "codingagent_qa",
        "instruction": "loss 怎么算的？",
        "workspace_path": str(repo),
    })
    assert out.ok
    assert out.state_patch["add_artifacts"][0]["source"] == "codingagent_qa"


def test_consult_codingagent_qa_requires_path(stack):
    _, _, tools, conv = stack
    out = tools.execute(conv, "consult_expert", {
        "expert": "codingagent_qa",
        "instruction": "loss 怎么算的？",
    })
    assert not out.ok
    assert "workspace_path" in out.text


def test_consult_unknown_and_side_effect_experts_rejected(stack):
    _, _, tools, conv = stack
    out = tools.execute(conv, "consult_expert",
                        {"expert": "no_such", "instruction": "x"})
    assert not out.ok
    out = tools.execute(conv, "consult_expert",
                        {"expert": "reproagent", "instruction": "x"})
    assert not out.ok and "side_effects" in out.text


# ── runs inspection ───────────────────────────────────────────────────────────

def test_list_and_inspect_runs(stack):
    cfg, _, tools, conv = stack
    assert "No research runs" in tools.execute(conv, "list_runs", {}).text

    state = init_run(goal="Test goal", workspace_root=conv.workspace_root, config=cfg)
    out = tools.execute(conv, "list_runs", {})
    assert state.run.run_id in out.text

    out = tools.execute(conv, "inspect_run", {"run_id": state.run.run_id})
    assert out.ok
    assert out.state_patch["active_run_id"] == state.run.run_id


# ── Tier 2 gate: propose -> confirm -> start ──────────────────────────────────

def test_start_without_brief_rejected(stack):
    _, _, tools, conv = stack
    out = tools.execute(conv, "start_research_run", {})
    assert not out.ok and "No pending brief" in out.text


def test_propose_then_start_creates_run(stack):
    cfg, _, tools, conv = stack
    out = tools.execute(conv, "propose_research_run", {
        "brief": {"goal": "Verify X improves Y", "hypothesis": "h"},
    })
    assert out.ok
    # apply the brief_proposed event patch (ChatLoop does this in production)
    etype, payload = out.extra_events[0]
    assert etype == ConversationEventType.brief_proposed
    conv.apply_patch(payload["state_patch"])
    assert conv.pending_brief is not None

    out = tools.execute(conv, "start_research_run", {})
    assert out.ok, out.text
    kinds = [e[0] for e in out.extra_events]
    assert ConversationEventType.brief_confirmed in kinds
    assert ConversationEventType.run_created in kinds

    # apply patches like ChatLoop would
    for etype, payload in out.extra_events:
        conv.apply_patch(payload.get("state_patch", {}))
    assert conv.pending_brief is None
    assert conv.active_run_id is not None

    state = load_state(conv.workspace_root, conv.active_run_id)
    assert state is not None
    assert "Verify X improves Y" in state.run.research_goal
    assert "Hypothesis: h" in state.run.research_goal


def test_advance_run_injects_directive(stack):
    cfg, _, tools, conv = stack
    state = init_run(goal="Goal", workspace_root=conv.workspace_root, config=cfg)
    run_id = state.run.run_id

    out = tools.execute(conv, "advance_run", {
        "run_id": run_id,
        "instruction": "失败了的话换个 baseline 继续",
    })
    assert out.ok, out.text

    state = load_state(conv.workspace_root, run_id)
    assert len(state.user_directives) == 1
    assert state.user_directives[0].text == "失败了的话换个 baseline 继续"
    assert state.user_directives[0].source_conversation == conv.conversation_id

    # the planner must see the directive
    ctx = build_controller_context(state)
    assert "User Directives" in ctx
    assert "换个 baseline" in ctx


def test_advance_run_missing_run(stack):
    _, _, tools, conv = stack
    out = tools.execute(conv, "advance_run", {
        "run_id": "res-nonexistent", "instruction": "go",
    })
    assert not out.ok


def test_consult_artifacts_written_under_conversation(stack):
    _, _, tools, conv = stack
    tools.execute(conv, "consult_expert", {
        "expert": "expagent", "instruction": "idea 讨论：diffusion 用于时序检测",
    })
    conv_dir = Path(conv.workspace_root) / "conversations" / conv.conversation_id
    assert (conv_dir / "experts").exists()
