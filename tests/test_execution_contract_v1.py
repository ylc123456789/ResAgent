"""Freeze the cross-module milestone-one execution contract fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


FIXTURES = Path(__file__).parent / "fixtures" / "execution_contract_v1"
PHYSICAL_FIELDS = {
    "workspace_path", "external_repo_path", "copy_from", "env_name",
}
V2_CAPABILITIES = {
    "modify_code", "reproduce_experiment", "execute_experiment",
    "analyze_results", "search_literature", "ask_user",
}
LEGACY_V1_FIELDS = {"type", "plan", "workspace_intent", "kind", "priority"}


def _load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_current_session_binding_fixture() -> None:
    card = _load_yaml("session_current.yaml")
    bindings = card["bindings"]
    assert set(bindings) == {"repo", "environment", "dataset_cache", "pip_cache"}
    assert set(bindings["repo"]) == {"path", "origin", "commit", "mode"}
    assert bindings["repo"]["mode"] in {"isolated", "copy", "shared"}
    assert set(bindings["environment"]) == {
        "name", "policy", "certification", "certified_at", "audit_artifact",
    }
    assert bindings["environment"]["policy"] in {"auto", "reuse_only", "frozen"}
    assert bindings["environment"]["certification"] == "experiment"
    assert bindings["environment"]["certified_at"]
    assert bindings["environment"]["audit_artifact"]
    assert card["project_path"] != bindings["repo"]["path"]


def test_legacy_session_fixture_has_no_resource_registration() -> None:
    card = _load_yaml("session_legacy.yaml")
    assert "bindings" not in card
    assert card["project_path"]


def test_logical_plan_uses_capability_without_physical_or_v1_fields() -> None:
    actions = _load_json("logical_plan.json")["recommended_actions"]
    action_ids = [action["action_id"] for action in actions]
    assert all(action_ids)
    assert len(action_ids) == len(set(action_ids))

    seen: set[str] = set()
    for action in actions:
        assert action["capability"] in V2_CAPABILITIES
        assert set(action["depends_on"]) <= seen
        assert not (set(_walk_keys(action)) & PHYSICAL_FIELDS)
        assert not (set(_walk_keys(action)) & LEGACY_V1_FIELDS)
        seen.add(action["action_id"])

    project_actions = [
        a for a in actions if a["capability"] != "analyze_results"
    ]
    assert {a["project_ref"] for a in project_actions} == {"example_repo"}


def test_workspace_source_fixture_freezes_exclusive_selection() -> None:
    cases = _load_json("workspace_sources.json")

    def explicit_count(case: dict) -> int:
        return sum(bool(case.get(field)) for field in (
            "repo_url", "copy_from", "external_repo_path",
        ))

    assert all(explicit_count(case) == 1 for case in cases["valid_new_tasks"])
    assert all(explicit_count(case) != 1 for case in cases["invalid_new_tasks"])
    assert explicit_count(cases["valid_resume"]) == 0
    assert cases["valid_resume"]["existing_workspace_repo"] is True
