"""Tests for the capability registry (agent.yaml cards)."""

import textwrap

from resagent.capabilities import CapabilityRegistry
from resagent.config import Config


def _registry(cfg: Config) -> CapabilityRegistry:
    reg = CapabilityRegistry(cfg)
    reg.load()
    return reg


def test_registry_builtin_cards():
    reg = _registry(Config())
    assert set(reg.cards) >= {"expagent", "codingagent", "codingagent_qa", "reproagent"}
    assert reg.get("expagent").side_effects == "none"
    assert reg.get("codingagent_qa").status == "available"
    assert reg.get("reproagent").side_effects == "workspace_and_environment"
    assert reg.get("reproagent").requires_confirmation is True


def test_router_descriptions_render():
    text = _registry(Config()).router_descriptions()
    assert "expagent" in text
    assert "codingagent_qa" in text
    assert "side_effects" in text


def test_check_callable_tier1():
    reg = _registry(Config())
    ok, _ = reg.check_callable("expagent", tier=1)
    assert ok
    ok, reason = reg.check_callable("codingagent", tier=1)  # workspace side effects
    assert not ok and "side_effects" in reason
    ok, reason = reg.check_callable("reproagent", tier=1)
    assert not ok
    ok, reason = reg.check_callable("nonexistent", tier=1)
    assert not ok and "Unknown expert" in reason


def test_check_callable_planned():
    cfg = Config()
    cfg.agents.cards = {"codingagent_qa": {"status": "planned"}}
    reg = _registry(cfg)
    ok, reason = reg.check_callable("codingagent_qa", tier=1)
    assert not ok and "planned" in reason


def test_registry_card_from_repo(tmp_path):
    """Repo-provided agent.yaml overrides the built-in card."""
    module_dir = tmp_path / "FakeExpAgent"
    module_dir.mkdir()
    (module_dir / "agent.yaml").write_text(textwrap.dedent("""\
        name: expagent
        version: "9.9"
        role: scientific_advisor
        description_for_router: "REPO OVERRIDE DESCRIPTION"
        capabilities: [scientific_advisory]
        side_effects: none
        status: available
    """), encoding="utf-8")

    cfg = Config()
    cfg.agents.expagent = str(module_dir)
    reg = _registry(cfg)
    card = reg.get("expagent")
    assert card.version == "9.9"
    assert "REPO OVERRIDE" in card.description_for_router


def test_registry_config_card_override():
    cfg = Config()
    cfg.agents.cards = {"expagent": {"description_for_router": "CONFIG OVERRIDE"}}
    reg = _registry(cfg)
    assert "CONFIG OVERRIDE" in reg.get("expagent").description_for_router
    # unspecified fields inherited from built-in
    assert reg.get("expagent").side_effects == "none"


def test_invalid_side_effects_coerced(tmp_path):
    """Non-standard side_effects vocab coerced to the safe 'workspace'."""
    module_dir = tmp_path / "FakeCoding"
    module_dir.mkdir()
    (module_dir / "agent.yaml").write_text(textwrap.dedent("""\
        name: codingagent
        description_for_router: "dual-mode card"
        side_effects: none_for_question__workspace_for_modification
    """), encoding="utf-8")

    cfg = Config()
    cfg.agents.codingagent = str(module_dir)
    reg = _registry(cfg)
    card = reg.get("codingagent")
    assert card.side_effects == "workspace"
    assert any("coerced" in w for w in reg.warnings)
    # and it is therefore NOT tier-1 callable
    ok, _ = reg.check_callable("codingagent", tier=1)
    assert not ok
