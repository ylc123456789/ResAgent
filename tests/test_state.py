"""Tests for state.py — persistence and atomicity."""

import json
import tempfile
from pathlib import Path

import pytest
from resagent.models import (
    ResearchRun, ResearchState, Artifact, ArtifactType, Producer,
)
from resagent.state import (
    init_state, save_state, load_state, state_path, workspace_path,
)


class TestStateIO:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()

    def test_init_state(self):
        state = init_state("test-001", self.tmp, "Test goal")
        assert state.run.run_id == "test-001"
        assert state.run.research_goal == "Test goal"
        assert state.run.status.value == "running"

    def test_save_and_load(self):
        state = init_state("test-002", self.tmp, "Test goal")
        state.current_summary = "Working on it"
        a = Artifact(
            id="a1", type=ArtifactType.report,
            producer=Producer.ResAgent, path="p", summary="s",
        )
        state.artifacts.append(a)
        save_state(state)

        loaded = load_state(self.tmp, "test-002")
        assert loaded is not None
        assert loaded.current_summary == "Working on it"
        assert len(loaded.artifacts) == 1
        assert loaded.run.updated_at is not None

    def test_load_nonexistent(self):
        assert load_state("/tmp/nonexistent", "no-such-run") is None

    def test_state_path(self):
        sp = state_path("/workspace", "run-001")
        assert sp.name == "state.json"
        assert "run-001" in str(sp)

    def test_save_preserves_disk_format(self):
        """Verify saved state.json is valid JSON."""
        state = init_state("test-003", self.tmp, "Test")
        save_state(state)
        sp = state_path(self.tmp, "test-003")
        data = json.loads(sp.read_text())
        assert data["run"]["run_id"] == "test-003"
