"""Tests for adapters — mock mode only."""

import sys
import tempfile
from pathlib import Path

from resagent.models import (
    ResearchState, ResearchRun, AgentTask, Producer, AgentKind,
)
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter
from resagent.workspace_layout import WorkspaceLayout


def _make_state():
    run = ResearchRun(
        run_id="test-adapter-001", workspace_dir="/tmp/runs",
        research_goal="Test research",
    )
    return ResearchState(run=run)


def _make_layout():
    return WorkspaceLayout(tempfile.mkdtemp(), "test-adapter-001")


class TestExpAgentAdapter:
    def test_mock_advise(self):
        adapter = ExpAgentAdapter(mock=True)
        state = _make_state()
        result = adapter.advise(state, _make_layout())

        assert result["artifact"] is not None
        assert result["artifact"].producer == Producer.ExpAgent
        assert len(result["tasks"]) > 0
        assert result["tasks"][0].agent in (
            Producer.CodingAgent, Producer.ReproAgent
        )

    def test_mock_creates_tasks(self):
        adapter = ExpAgentAdapter(mock=True)
        state = _make_state()
        result = adapter.advise(state, _make_layout())

        tasks = result["tasks"]
        kinds = {t.kind for t in tasks}
        assert AgentKind.coding_task in kinds or AgentKind.repro_task in kinds


class TestCodingAgentAdapter:
    def test_mock_execute(self):
        adapter = CodingAgentAdapter(mock=True)
        task = AgentTask(
            id="task_001", agent=Producer.CodingAgent,
            kind=AgentKind.coding_task,
            input={"repo_path": "/tmp", "task_goal": "test coding task"}
        )
        result = adapter.execute(task, _make_layout())

        assert result["artifact"] is not None
        assert result["artifact"].producer == Producer.CodingAgent
        assert "test coding task" in result["artifact"].summary


class TestReproAgentAdapter:
    def test_mock_execute(self):
        adapter = ReproAgentAdapter(mock=True)
        task = AgentTask(
            id="task_002", agent=Producer.ReproAgent,
            kind=AgentKind.repro_task,
            input={
                "paper_url": "https://arxiv.org/abs/1234",
                "repo_url": "https://github.com/x/y",
                "experiment_goal": "reproduce baseline",
            }
        )
        result = adapter.execute(task, _make_layout())

        assert result["artifact"] is not None
        assert result["artifact"].producer == Producer.ReproAgent
        assert result["outcome"] == "completed"

    def _install_fake_reproagent(self, tmp_path, monkeypatch):
        """Install a fake `reproagent` package that records the ReproTask."""
        pkg = tmp_path / "fake_repro" / "reproagent"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "models.py").write_text(
            "LAST_KW = {}\n"
            "class ReproTask:\n"
            "    def __init__(self, **kw):\n"
            "        LAST_KW.clear(); LAST_KW.update(kw)\n"
        )
        (pkg / "controller.py").write_text(
            "def run_controller(task):\n"
            "    class S:\n"
            "        status = 'completed'\n"
            "        final_summary = 'ok'\n"
            "        steps = []\n"
            "    return S()\n"
        )
        for mod in ("reproagent.controller", "reproagent.models", "reproagent"):
            monkeypatch.delitem(sys.modules, mod, raising=False)
        return tmp_path / "fake_repro"

    def test_dataset_cache_dir_plumbed_to_repro_task(self, tmp_path, monkeypatch):
        """Regression: dataset_cache_dir was silently dropped by the adapter,
        disabling reproagent's dataset cache under orchestration."""
        module_path = self._install_fake_reproagent(tmp_path, monkeypatch)
        adapter = ReproAgentAdapter(
            module_path=str(module_path), dataset_cache_dir="/data/cache",
        )
        adapter._call_execute(
            {"paper_url": "p", "repo_url": "r"}, tmp_path / "out",
        )
        from reproagent import models
        assert models.LAST_KW["dataset_cache_dir"] == "/data/cache"

    def test_task_level_dataset_cache_overrides_default(self, tmp_path, monkeypatch):
        module_path = self._install_fake_reproagent(tmp_path, monkeypatch)
        adapter = ReproAgentAdapter(
            module_path=str(module_path), dataset_cache_dir="/data/cache",
        )
        adapter._call_execute(
            {"paper_url": "p", "repo_url": "r",
             "dataset_cache_dir": "/task/override"},
            tmp_path / "out",
        )
        from reproagent import models
        assert models.LAST_KW["dataset_cache_dir"] == "/task/override"
