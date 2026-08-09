"""Tests for config loading, esp. workspace path resolution precedence."""

from resagent.config import load_config


def test_workspace_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RESAGENT_WORKSPACE", str(tmp_path / "central_ws"))
    cfg = load_config("")
    assert cfg.workspace.default_runs_dir == str(tmp_path / "central_ws")


def test_workspace_yaml_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RESAGENT_WORKSPACE", raising=False)
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("workspace:\n  default_runs_dir: /data/yaml_ws\n",
                         encoding="utf-8")
    cfg = load_config(str(yaml_path))
    assert cfg.workspace.default_runs_dir == "/data/yaml_ws"


def test_workspace_default_fallback(monkeypatch):
    monkeypatch.delenv("RESAGENT_WORKSPACE", raising=False)
    cfg = load_config("")
    assert cfg.workspace.default_runs_dir == "runs"


def test_env_beats_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("RESAGENT_WORKSPACE", "/data/env_ws")
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("workspace:\n  default_runs_dir: /data/yaml_ws\n",
                         encoding="utf-8")
    cfg = load_config(str(yaml_path))
    assert cfg.workspace.default_runs_dir == "/data/env_ws"


def test_repro_dataset_cache_env(monkeypatch):
    """reproagent's own env-var convention must flow into config."""
    monkeypatch.setenv("REPROAGENT_DATASET_CACHE", "/root/autodl-tmp/datasets")
    cfg = load_config("")
    assert cfg.policy.repro_dataset_cache == "/root/autodl-tmp/datasets"


def test_repro_dataset_cache_yaml(monkeypatch, tmp_path):
    monkeypatch.delenv("REPROAGENT_DATASET_CACHE", raising=False)
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("policy:\n  repro_dataset_cache: /data/ds\n",
                         encoding="utf-8")
    cfg = load_config(str(yaml_path))
    assert cfg.policy.repro_dataset_cache == "/data/ds"


def test_repro_dataset_cache_default_empty(monkeypatch):
    monkeypatch.delenv("REPROAGENT_DATASET_CACHE", raising=False)
    cfg = load_config("")
    assert cfg.policy.repro_dataset_cache == ""
