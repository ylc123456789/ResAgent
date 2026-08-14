"""Tests for chat-layer models (docs/CONVERSATION_LAYER_DESIGN.md §4.2)."""

from resagent.conversation.models import (
    ConvArtifactRef,
    ConversationState,
    ExpertCard,
    ResearchBrief,
)


def test_conv_models_roundtrip():
    conv = ConversationState(
        conversation_id="conv-20260808-abc123",
        workspace_root="/tmp/ws",
        active_run_id="res-20260808-x1",
        recent_artifacts=[ConvArtifactRef(id="exp_consult_001", source="expagent")],
        pending_brief=ResearchBrief(goal="Test goal", hypothesis="h"),
    )
    restored = ConversationState.model_validate_json(conv.model_dump_json())
    assert restored.conversation_id == conv.conversation_id
    assert restored.active_run_id == "res-20260808-x1"
    assert restored.recent_artifacts[0].id == "exp_consult_001"
    assert restored.pending_brief.goal == "Test goal"


def test_brief_render_goal_text():
    brief = ResearchBrief(
        goal="Verify X improves Y",
        hypothesis="X improves Y on Z",
        context_summary="Discussed in conversation",
        constraints=["budget 1 GPU"],
    )
    text = brief.render_goal_text()
    assert "Verify X improves Y" in text
    assert "Hypothesis:" in text
    assert "Background:" in text
    assert "Constraints: budget 1 GPU" in text


def test_brief_render_display_contains_goal():
    brief = ResearchBrief(goal="g", suggested_first_step="reproduce baseline")
    display = brief.render_display()
    assert "Goal: g" in display
    assert "reproduce baseline" in display


def test_expert_card_confirmation_derived():
    c1 = ExpertCard(name="a", side_effects="none")
    assert c1.requires_confirmation is False
    c2 = ExpertCard(name="b", side_effects="workspace_and_environment")
    assert c2.requires_confirmation is True


def test_expert_card_router_line():
    card = ExpertCard(
        name="expagent", role="scientific_advisor", side_effects="none",
        capabilities=["scientific_advisory"],
        description_for_router="科学顾问。",
    )
    line = card.router_line()
    assert "expagent" in line
    assert "side_effects: none" in line
    assert "科学顾问" in line


def test_brief_tolerant_coercion():
    """Real-API finding: LLMs pass constraints as prose strings, artifacts as
    bare paths. Accept and coerce instead of rejecting."""
    brief = ResearchBrief.model_validate({
        "goal": "g",
        "constraints": "实验规模要小；预算有限",
        "relevant_artifacts": ["/tmp/repo/train.py", {"id": "a1", "summary": "s"}],
    })
    assert brief.constraints == ["实验规模要小；预算有限"]
    assert brief.relevant_artifacts[0].path == "/tmp/repo/train.py"
    assert brief.relevant_artifacts[1].id == "a1"


def test_state_patch_application():
    conv = ConversationState(conversation_id="c1", workspace_root="/tmp/ws")
    conv.apply_patch({"active_run_id": "res-1"})
    conv.apply_patch({"add_artifacts": [
        {"id": "a1", "summary": "s", "source": "expagent"},
    ]})
    conv.apply_patch({"pending_brief": {"goal": "g"}})
    assert conv.active_run_id == "res-1"
    assert conv.recent_artifacts[0].id == "a1"
    assert conv.pending_brief.goal == "g"
    conv.apply_patch({"pending_brief": None})
    assert conv.pending_brief is None
