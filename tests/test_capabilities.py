"""Tests for the capability registry (agent.yaml cards)."""

import textwrap

import pytest

from resagent.capabilities import CapabilityError, CapabilityRegistry
from resagent.config import Config
from resagent.models import Producer


def _registry(cfg: Config) -> CapabilityRegistry:
    reg = CapabilityRegistry(cfg)
    reg.load()
    return reg


def _module(tmp_path, name: str, capabilities: str, **extra) -> str:
    module_dir = tmp_path / name
    module_dir.mkdir()
    extra_yaml = "\n".join(f"{k}: {v}" for k, v in extra.items())
    (module_dir / "agent.yaml").write_text(
        textwrap.dedent(f"""\
            name: {name}
            role: test_role
            capabilities: [{capabilities}]
            side_effects: none
            status: available
        """) + extra_yaml,
        encoding="utf-8",
    )
    return str(module_dir)


def _configured_registry(tmp_path) -> CapabilityRegistry:
    cfg = Config()
    cfg.agents.expagent = _module(
        tmp_path, "expagent", "analyze_results, search_literature",
    )
    cfg.agents.codingagent = _module(tmp_path, "codingagent", "modify_code",
                                     side_effects="workspace")
    cfg.agents.reproagent = _module(
        tmp_path, "reproagent", "reproduce_experiment, execute_experiment",
        side_effects="workspace_and_environment",
    )
    return _registry(cfg)


def test_builtin_cards_only_resagent_owned():
    reg = _registry(Config())
    # Executor cards come from module agent.yaml, not built-ins.
    assert reg.cards.keys() == {"codingagent_qa"}


def test_registry_loads_executor_cards_from_modules(tmp_path):
    reg = _configured_registry(tmp_path)
    assert reg.get("expagent") is not None
    assert reg.get("codingagent") is not None
    assert reg.get("reproagent") is not None
    assert reg.get("reproagent").side_effects == "workspace_and_environment"


def test_resolve_routes_all_six_capabilities(tmp_path):
    reg = _configured_registry(tmp_path)
    assert reg.resolve("modify_code") == Producer.CodingAgent
    assert reg.resolve("reproduce_experiment") == Producer.ReproAgent
    assert reg.resolve("execute_experiment") == Producer.ReproAgent
    assert reg.resolve("analyze_results") == Producer.ExpAgent
    assert reg.resolve("search_literature") == Producer.ExpAgent
    assert reg.resolve("ask_user") == Producer.ResAgent


def test_resolve_missing_capability_fails_closed(tmp_path):
    reg = _registry(Config())  # only codingagent_qa built-in
    with pytest.raises(CapabilityError):
        reg.resolve("modify_code")


def test_resolve_conflicting_owners_fails_closed(tmp_path):
    cfg = Config()
    cfg.agents.expagent = _module(tmp_path, "expagent", "analyze_results")
    # A second module also declares analyze_results -> conflict.
    cfg.agents.reproagent = _module(tmp_path, "reproagent", "analyze_results")
    reg = _registry(cfg)
    with pytest.raises(CapabilityError):
        reg.resolve("analyze_results")


def test_card_owner_is_the_routing_source(tmp_path):
    cfg = Config()
    # There is no second capability->executor table overriding the card owner.
    cfg.agents.expagent = _module(tmp_path, "expagent", "modify_code")
    reg = _registry(cfg)
    assert reg.resolve("modify_code") == Producer.ExpAgent


def test_complete_registry_validation_fails_before_dispatch(tmp_path):
    cfg = Config()
    cfg.agents.expagent = _module(
        tmp_path, "expagent", "analyze_results, search_literature",
    )
    reg = _registry(cfg)
    with pytest.raises(CapabilityError, match="invalid scientific capability registry"):
        reg.validate_scientific_routing()


def test_complete_registry_validation_accepts_all_cards(tmp_path):
    _configured_registry(tmp_path).validate_scientific_routing()


def test_controller_summary_lists_vocabulary(tmp_path):
    reg = _configured_registry(tmp_path)
    summary = reg.controller_summary()
    assert "modify_code" in summary
    assert "CodingAgent" in summary
    assert "analyze_results" in summary
    assert "ExpAgent" in summary


def test_router_descriptions_render():
    # codingagent_qa is always present as a built-in.
    text = _registry(Config()).router_descriptions()
    assert "codingagent_qa" in text
    assert "side_effects" in text


def test_check_callable_tier1(tmp_path):
    reg = _configured_registry(tmp_path)
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


def test_registry_card_from_repo_overrides(tmp_path):
    """Repo-provided agent.yaml fully defines the card (no built-in merge)."""
    module_dir = tmp_path / "FakeExpAgent"
    module_dir.mkdir()
    (module_dir / "agent.yaml").write_text(textwrap.dedent("""\
        name: expagent
        version: "9.9"
        role: scientific_advisor
        description_for_router: "REPO OVERRIDE DESCRIPTION"
        capabilities: [analyze_results, search_literature]
        side_effects: none
        status: available
    """), encoding="utf-8")

    cfg = Config()
    cfg.agents.expagent = str(module_dir)
    reg = _registry(cfg)
    card = reg.get("expagent")
    assert card.version == "9.9"
    assert "REPO OVERRIDE" in card.description_for_router


def test_invalid_side_effects_coerced(tmp_path):
    """Non-standard side_effects vocab coerced to the safe 'workspace'."""
    module_dir = tmp_path / "FakeCoding"
    module_dir.mkdir()
    (module_dir / "agent.yaml").write_text(textwrap.dedent("""\
        name: codingagent
        description_for_router: "dual-mode card"
        capabilities: [modify_code]
        side_effects: none_for_question__workspace_for_modification
    """), encoding="utf-8")

    cfg = Config()
    cfg.agents.codingagent = str(module_dir)
    reg = _registry(cfg)
    card = reg.get("codingagent")
    assert card.side_effects == "workspace"
    assert any("coerced" in w for w in reg.warnings)
    ok, _ = reg.check_callable("codingagent", tier=1)
    assert not ok
