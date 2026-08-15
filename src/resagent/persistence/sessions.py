"""Read sub-agent session index cards (session.yaml).

The card FORMAT is the cross-module contract (docs/reference/SESSION_AND_PROJECT_MODEL.md
§3) — ResAgent reads cards itself here instead of importing sub-module code.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def read_session_card(path: str | Path) -> dict | None:
    """Read one session.yaml. Returns None if missing/unparseable."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def scan_session_cards(root: str | Path, limit: int = 50) -> list[dict]:
    """Recursively find session.yaml files under root. Newest first."""
    r = Path(root)
    if not r.is_dir():
        return []
    cards = []
    for p in sorted(r.rglob("session.yaml")):
        card = read_session_card(p)
        if card is None:
            continue
        card["_manifest_path"] = str(p)
        cards.append(card)
        if len(cards) >= limit:
            break
    cards.sort(key=lambda c: str(c.get("updated_at", "")), reverse=True)
    return cards


def card_to_session_ref(card: dict, manifest_path: str, run_id: str = "") -> dict:
    """Project a card dict into a SessionRef-compatible dict."""
    return {
        "module": str(card.get("module", "")),
        "session_id": str(card.get("session_id", "")),
        "manifest_path": manifest_path,
        "status": str(card.get("status", "")),
        "summary": str(card.get("summary", ""))[:200],
        "run_id": run_id,
    }


def write_mock_card(path: str | Path, *, module: str, session_id: str,
                    kind: str = "task_session", status: str = "completed",
                    summary: str = "mock session",
                    parent: dict | None = None) -> None:
    """Write a minimal card. Used by adapter mock paths so mock end-to-end
    flows exercise the same index machinery as real runs."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "module": module,
        "kind": kind,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "summary": summary,
        "parent": parent,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
