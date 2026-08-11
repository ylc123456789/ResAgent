"""Tests for the sub-session index card machinery + conversation index."""

from pathlib import Path

import yaml

from resagent.chat_models import ConversationState
from resagent.session_cards import (
    card_to_session_ref,
    read_session_card,
    scan_session_cards,
    write_mock_card,
)


def _write_card(path: Path, **overrides):
    card = {
        "schema_version": 1,
        "session_id": "repro-x1",
        "module": "reproagent",
        "kind": "task_session",
        "status": "completed",
        "summary": "MNIST 3ep 99.04%",
        "updated_at": "2026-08-10T16:00:00Z",
        "bindings": {"conda_env": "resenv_x", "dataset_cache": "/data"},
    }
    card.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(card, allow_unicode=True), encoding="utf-8")


def test_read_card_ok(tmp_path):
    p = tmp_path / "ws" / "session.yaml"
    _write_card(p)
    card = read_session_card(p)
    assert card["module"] == "reproagent"
    assert card["bindings"]["conda_env"] == "resenv_x"


def test_read_card_tolerates_garbage(tmp_path):
    p = tmp_path / "session.yaml"
    p.write_text("{{{ not yaml", encoding="utf-8")
    assert read_session_card(p) is None
    assert read_session_card(tmp_path / "missing.yaml") is None


def test_scan_cards_newest_first(tmp_path):
    _write_card(tmp_path / "a" / "session.yaml", session_id="s1",
                updated_at="2026-08-09T10:00:00Z")
    _write_card(tmp_path / "b" / "session.yaml", session_id="s2",
                updated_at="2026-08-10T10:00:00Z")
    cards = scan_session_cards(tmp_path)
    assert [c["session_id"] for c in cards] == ["s2", "s1"]
    assert cards[0]["_manifest_path"].endswith("session.yaml")


def test_card_to_session_ref(tmp_path):
    p = tmp_path / "ws" / "session.yaml"
    _write_card(p)
    ref = card_to_session_ref(read_session_card(p), str(p), run_id="res-1")
    assert ref == {
        "module": "reproagent", "session_id": "repro-x1",
        "manifest_path": str(p), "status": "completed",
        "summary": "MNIST 3ep 99.04%", "run_id": "res-1",
    }


def test_session_index_patch_upsert_and_cap():
    conv = ConversationState(conversation_id="c1", workspace_root="/tmp/ws")
    conv.apply_patch({"add_sessions": [
        {"module": "reproagent", "session_id": "s1", "manifest_path": "/x/session.yaml",
         "status": "running"},
    ]})
    assert len(conv.session_index) == 1
    # upsert: same (module, session_id) refreshes instead of duplicating
    conv.apply_patch({"add_sessions": [
        {"module": "reproagent", "session_id": "s1", "manifest_path": "/x/session.yaml",
         "status": "completed", "summary": "done"},
    ]})
    assert len(conv.session_index) == 1
    assert conv.session_index[0].status == "completed"
    # different module with same id is a different entry
    conv.apply_patch({"add_sessions": [
        {"module": "codingagent", "session_id": "s1", "manifest_path": "/y/session.yaml"},
    ]})
    assert len(conv.session_index) == 2


def test_write_mock_card(tmp_path):
    p = tmp_path / "sub" / "session.yaml"
    write_mock_card(p, module="expagent", session_id="exp-1", kind="advisory_session")
    card = read_session_card(p)
    assert card["module"] == "expagent"
    assert card["session_id"] == "exp-1"
