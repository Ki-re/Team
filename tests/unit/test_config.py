from __future__ import annotations

import os
from pathlib import Path

import team_mcp.config as config_mod
from team_mcp.config import _bool, _split_paths, load_config


def test_split_paths_empty_string_returns_empty_list():
    assert _split_paths("") == []
    assert _split_paths("   ") == []


def test_split_paths_splits_on_platform_separator(tmp_path: Path):
    sep = ";" if os.name == "nt" else ":"
    raw = f"{tmp_path / 'a'}{sep}{tmp_path / 'b'}"
    result = _split_paths(raw)
    assert len(result) == 2
    assert all(p.is_absolute() for p in result)


def test_bool_parses_truthy_values():
    for v in ("1", "true", "True", "yes", "on", "  TRUE  "):
        assert _bool(v, default=False) is True


def test_bool_parses_falsy_and_unknown_as_false():
    for v in ("0", "false", "no", "off", "garbage"):
        assert _bool(v, default=True) is False


def test_bool_uses_default_when_empty():
    assert _bool("", default=True) is True
    assert _bool("", default=False) is False


def _isolated_load_config(tmp_path: Path, monkeypatch, env: dict[str, str]):
    """load_config crea de verdad el directorio padre de ledger_db/cache_db
    en disco — sin aislar REPO_ROOT, los tests dejarían carpetas reales
    dentro de este repo. Se apunta a un tmp_path propio en su lugar."""
    fake_repo_root = tmp_path / "fake_repo"
    fake_repo_root.mkdir()
    monkeypatch.setattr(config_mod, "REPO_ROOT", fake_repo_root)
    return load_config(env=env), fake_repo_root


def test_load_config_sandbox_roots_always_includes_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    config, _ = _isolated_load_config(tmp_path, monkeypatch, {})
    assert cwd.resolve() in config.sandbox_roots


def test_load_config_sandbox_roots_also_includes_explicit_env(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    extra = tmp_path / "extra"
    extra.mkdir()
    config, _ = _isolated_load_config(tmp_path, monkeypatch, {"TEAM_SANDBOX_ROOTS": str(extra)})
    assert cwd.resolve() in config.sandbox_roots
    assert extra.resolve() in config.sandbox_roots


def test_load_config_relative_ledger_db_anchored_to_repo_root_not_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    config, fake_repo_root = _isolated_load_config(
        tmp_path, monkeypatch, {"TEAM_LEDGER_DB": "some/relative/path.sqlite3"},
    )
    assert config.ledger_db == fake_repo_root / "some/relative/path.sqlite3"
    assert cwd not in config.ledger_db.parents


def test_load_config_defaults_token_budget(tmp_path, monkeypatch):
    config, _ = _isolated_load_config(tmp_path, monkeypatch, {})
    assert config.token_budget_default == 200_000
