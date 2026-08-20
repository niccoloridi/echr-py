"""Tests for hudoc_py.config – primarily the API-key resolution chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from hudoc_py import __version__, config


def test_version_present():
    assert isinstance(__version__, str) and len(__version__) > 0


def test_endpoints_defined():
    assert config.HUDOC_SEARCH_URL.startswith("https://hudoc.echr.coe.int")
    assert config.HUDOC_EXEC_SEARCH_URL.startswith("https://hudoc.exec.coe.int")


def test_gemini_key_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    # Even if a Resources file exists, env wins.
    monkeypatch.setattr(config, "_resources_dir", lambda: tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text("file-key")
    assert config.get_gemini_api_key() == "env-key"


def test_gemini_key_falls_back_to_resources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(config, "_resources_dir", lambda: tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text("file-key\n")
    assert config.get_gemini_api_key() == "file-key"


def test_gemini_key_missing_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(config, "_resources_dir", lambda: tmp_path)
    assert config.get_gemini_api_key() is None


def test_gemini_key_missing_required_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(config, "_resources_dir", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        config.get_gemini_api_key(required=True)
