"""Tests for auditable cloud acceptance metadata."""

import importlib.util
from pathlib import Path


def _acceptance_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "cloud_acceptance.py"
    spec = importlib.util.spec_from_file_location("cloud_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_git_metadata_records_commit_branch_and_dirty_state():
    metadata = _acceptance_module()._git_metadata(
        Path(__file__).resolve().parents[1],
    )
    assert len(metadata["commit"]) == 40
    assert metadata["branch"]
    assert isinstance(metadata["dirty"], bool)
    assert metadata["path"]
