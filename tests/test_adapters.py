"""Tests for adapters — mock mode only."""

import sys
import tempfile
import types
from pathlib import Path

from resagent.models import (
    ResearchState, ResearchRun, AgentTask, Producer, AgentKind,
    Artifact, ArtifactType,
)
from resagent.adapters.expagent import ExpAgentAdapter
from resagent.adapters.codingagent import CodingAgentAdapter
from resagent.adapters.reproagent import ReproAgentAdapter
from resagent.persistence.workspace import WorkspaceLayout


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

    def test_fallback_artifacts_use_run_root_absolute_paths(
        self, tmp_path, monkeypatch,
    ):
        """Advisory review can read prior results without direct dependencies."""
        class FakeArtifactRef:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class FakeAdvisorContext:
            model_fields = {}

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        package = types.ModuleType("experiment_designer")
        models = types.ModuleType("experiment_designer.models")
        models.AdvisorContext = FakeAdvisorContext
        models.ArtifactRef = FakeArtifactRef
        monkeypatch.setitem(sys.modules, "experiment_designer", package)
        monkeypatch.setitem(sys.modules, "experiment_designer.models", models)

        run_id = "artifact-review"
        state = ResearchState(run=ResearchRun(
            run_id=run_id,
            workspace_dir=str(tmp_path),
            research_goal="Review the reproduction result",
        ))
        result_path = tmp_path / run_id / "tasks" / "repro" / "result.md"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("final accuracy: 99.08%", encoding="utf-8")
        state.artifacts.append(Artifact(
            id="repro_result_001",
            type=ArtifactType.repro_result,
            producer=Producer.ReproAgent,
            path="tasks/repro/result.md",
            summary="GPU reproduction completed",
        ))
        task = AgentTask(
            id="task_002",
            agent=Producer.ExpAgent,
            kind=AgentKind.advise,
            input={"task_goal": "Analyze the completed experiment"},
        )
        adapter = ExpAgentAdapter()
        monkeypatch.setattr(adapter, "_ensure_import", lambda: None)

        context = adapter._build_advisor_context(state, task)

        assert len(context.artifacts) == 1
        assert context.artifacts[0].path == str(result_path.resolve())
        assert Path(context.artifacts[0].path).read_text() == "final accuracy: 99.08%"


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

    def test_environment_and_artifact_bindings_reach_repro_task(
        self, tmp_path, monkeypatch,
    ):
        module_path = self._install_fake_reproagent(tmp_path, monkeypatch)
        adapter = ReproAgentAdapter(module_path=str(module_path))
        artifacts = [{"path": "/runs/result.json", "description": "baseline"}]

        adapter._call_execute(
            {
                "paper_url": "p", "repo_url": "r", "env_name": "certified-env",
                "input_artifacts": artifacts,
            },
            tmp_path / "out",
        )

        from reproagent import models
        assert models.LAST_KW["env_name"] == "certified-env"
        assert models.LAST_KW["input_artifacts"] == artifacts

    def test_blocked_state_collects_coding_issues_from_observations(
        self, tmp_path, monkeypatch,
    ):
        """Blocked issues live on AgentObservation, not AgentState."""
        pkg = tmp_path / "blocked_repro" / "reproagent"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "models.py").write_text(
            "class ReproTask:\n"
            "    def __init__(self, **kw): self.kw = kw\n"
        )
        (pkg / "controller.py").write_text(
            "class Observation:\n"
            "    coding_issues = ['missing metric', 'missing metric', 'bad CLI']\n"
            "def run_controller(task):\n"
            "    class State:\n"
            "        status = 'blocked'\n"
            "        final_summary = 'patch required'\n"
            "        steps = [Observation()]\n"
            "        repo_context = None\n"
            "    return State()\n"
        )
        for mod in ("reproagent.controller", "reproagent.models", "reproagent"):
            monkeypatch.delitem(sys.modules, mod, raising=False)

        adapter = ReproAgentAdapter(module_path=str(tmp_path / "blocked_repro"))
        raw, outcome = adapter._call_execute(
            {"paper_url": "p", "repo_url": "r"}, tmp_path / "out",
        )

        assert outcome == "blocked"
        assert raw["status"] == "blocked"
        assert raw["coding_issues"] == ["missing metric", "bad CLI"]
