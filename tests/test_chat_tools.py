"""Tests for the 5 chat tools (mock adapters, no API calls)."""

import json
from pathlib import Path

import pytest

from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter
from resagent.capabilities import CapabilityRegistry
from resagent.conversation.models import ConversationEventType
from resagent.conversation.tools import ChatTools
from resagent.config import Config
from resagent.context import build_controller_context
from resagent.conversation import new_conversation
from resagent.orchestrator import build_controller, init_run
from resagent.persistence.state import load_state


def _configure_module_cards(cfg):
    """Point config at fake module agent.yaml files so the registry resolves.

    Uses a separate temp dir (not the conversation workspace root) so the fake
    module checkouts are not mistaken for research-run directories.
    """
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="resagent-modules-"))

    def write(name, capabilities, side_effects):
        d = root / name
        d.mkdir()
        (d / "agent.yaml").write_text(
            f"name: {name}\n"
            f"role: test\n"
            f"capabilities: [{capabilities}]\n"
            f"side_effects: {side_effects}\n"
            f"status: available\n",
            encoding="utf-8",
        )
        return str(d)

    cfg.agents.expagent = write("expagent", "analyze_results, search_literature", "none")
    cfg.agents.codingagent = write("codingagent", "modify_code", "workspace")
    cfg.agents.reproagent = write(
        "reproagent", "reproduce_experiment, execute_experiment",
        "workspace_and_environment",
    )


@pytest.fixture
def stack(tmp_path):
    cfg = Config()
    _configure_module_cards(cfg)
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


# ── sub-session index & resume ────────────────────────────────────────────────

def test_consult_indexes_session(stack):
    """Mock consult writes a session card; the patch must index it."""
    _, _, tools, conv = stack
    out = tools.execute(conv, "consult_expert", {
        "expert": "expagent", "instruction": "diffusion 用于时序检测有戏吗？",
    })
    assert out.ok
    sessions = out.state_patch.get("add_sessions")
    assert sessions and sessions[0]["module"] == "expagent"
    assert sessions[0]["manifest_path"].endswith("session.yaml")


def test_start_run_indexes_subsessions(stack):
    cfg, _, tools, conv = stack
    out = tools.execute(conv, "propose_research_run", {"brief": {"goal": "验证 X"}})
    conv.apply_patch(out.extra_events[0][1]["state_patch"])
    out = tools.execute(conv, "start_research_run", {})
    assert out.ok, out.text
    sessions = out.state_patch.get("add_sessions", [])
    assert sessions, "run sessions should be scanned into the index"
    assert all(s["run_id"] for s in sessions)
    manifests = [s["manifest_path"] for s in sessions]
    assert any("session.yaml" in m for m in manifests)


def test_list_sessions(stack):
    cfg, _, tools, conv = stack
    state = init_run(goal="g", workspace_root=conv.workspace_root, config=cfg)
    from resagent.persistence.sessions import write_mock_card
    ws = Path(conv.workspace_root) / state.run.run_id / "tasks" / "reproagent" / "task_001"
    write_mock_card(ws / "session.yaml", module="reproagent",
                    session_id="repro-1", summary="MNIST 99%")
    out = tools.execute(conv, "list_sessions", {"run_id": state.run.run_id})
    assert "repro-1" in out.text
    assert "reproagent" in out.text


def _seed_session(conv, module="reproagent", session_id="repro-1"):
    """Create a fake session card inside the workspace and index it."""
    ws = Path(conv.workspace_root) / "res-x" / "task_ws"
    ws.mkdir(parents=True)
    (ws / "state.json").write_text("{}", encoding="utf-8")
    from resagent.persistence.sessions import write_mock_card
    write_mock_card(ws / "session.yaml", module=module, session_id=session_id)
    conv.apply_patch({"add_sessions": [{
        "module": module, "session_id": session_id,
        "manifest_path": str(ws / "session.yaml"), "status": "completed",
    }]})
    return ws


def test_resume_subsession_dispatches_by_module(stack):
    _, _, tools, conv = stack
    _seed_session(conv, module="reproagent", session_id="repro-1")
    out = tools.execute(conv, "resume_subsession", {
        "session_id": "repro-1", "instruction": "再跑 5 个 epoch",
    })
    assert out.ok, out.text
    assert "repro-1" in out.text
    # index refreshed from the card
    assert conv.session_index[0].session_id == "repro-1"


def test_resume_subsession_unknown_session(stack):
    _, _, tools, conv = stack
    out = tools.execute(conv, "resume_subsession", {
        "session_id": "nope", "instruction": "继续",
    })
    assert not out.ok and "not in the index" in out.text


def test_resume_subsession_expagent_rejected(stack):
    _, _, tools, conv = stack
    _seed_session(conv, module="expagent", session_id="exp-1")
    out = tools.execute(conv, "resume_subsession", {
        "session_id": "exp-1", "instruction": "继续",
    })
    assert not out.ok and "not resumable" in out.text


def test_resume_subsession_containment(stack):
    """Sessions outside the workspace must not be resumable."""
    _, _, tools, conv = stack
    outside = Path(conv.workspace_root).parent / "outside_ws"
    outside.mkdir(exist_ok=True)
    from resagent.persistence.sessions import write_mock_card
    write_mock_card(outside / "session.yaml", module="reproagent", session_id="evil")
    out = tools.execute(conv, "resume_subsession", {
        "manifest_path": str(outside / "session.yaml"), "instruction": "继续",
    })
    assert not out.ok and "outside workspace" in out.text
