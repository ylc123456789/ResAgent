"""Tests for config loading, esp. workspace path resolution precedence.

Every test runs inside an empty tmp cwd: load_config("") probes ./config.yaml,
and we must not depend on whether the machine running the tests happens to
have a deployment config there (e.g. the AutoDL server checkout does).
"""

import pytest

from resagent.config import load_config


@pytest.fixture
def clean_cwd(monkeypatch, tmp_path):
    """Hermetic cwd: no config.yaml, no inherited env vars."""
    monkeypatch.chdir(tmp_path)
    for var in ("RESAGENT_WORKSPACE", "REPROAGENT_DATASET_CACHE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_workspace_env_override(clean_cwd, monkeypatch, tmp_path):
    monkeypatch.setenv("RESAGENT_WORKSPACE", str(tmp_path / "central_ws"))
    cfg = load_config("")
    assert cfg.workspace.default_runs_dir == str(tmp_path / "central_ws")


def test_workspace_yaml_used_when_no_env(clean_cwd, tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("workspace:\n  default_runs_dir: /data/yaml_ws\n",
                         encoding="utf-8")
    cfg = load_config(str(yaml_path))
    assert cfg.workspace.default_runs_dir == "/data/yaml_ws"


def test_workspace_default_fallback(clean_cwd):
    cfg = load_config("")
    assert cfg.workspace.default_runs_dir == "runs"


def test_env_beats_yaml(clean_cwd, monkeypatch):
    monkeypatch.setenv("RESAGENT_WORKSPACE", "/data/env_ws")
    yaml_path = clean_cwd / "config.yaml"
    yaml_path.write_text("workspace:\n  default_runs_dir: /data/yaml_ws\n",
                         encoding="utf-8")
    cfg = load_config(str(yaml_path))
    assert cfg.workspace.default_runs_dir == "/data/env_ws"


def test_repro_dataset_cache_env(clean_cwd, monkeypatch):
    """reproagent's own env-var convention must flow into config."""
    monkeypatch.setenv("REPROAGENT_DATASET_CACHE", "/root/autodl-tmp/datasets")
    cfg = load_config("")
    assert cfg.policy.repro_dataset_cache == "/root/autodl-tmp/datasets"


def test_repro_dataset_cache_yaml(clean_cwd, tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("policy:\n  repro_dataset_cache: /data/ds\n",
                         encoding="utf-8")
    cfg = load_config(str(yaml_path))
    assert cfg.policy.repro_dataset_cache == "/data/ds"


def test_repro_dataset_cache_default_empty(clean_cwd):
    cfg = load_config("")
    assert cfg.policy.repro_dataset_cache == ""
