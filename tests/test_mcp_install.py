"""Claude Desktop configuration tests for the local MCP installer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hudoc_py.mcp import install


@pytest.mark.parametrize(
    ("system", "expected_suffix"),
    [
        ("Darwin", Path("Library/Application Support/Claude/claude_desktop_config.json")),
        ("Linux", Path(".config/Claude/claude_desktop_config.json")),
    ],
)
def test_desktop_config_path_on_unix(monkeypatch, tmp_path, system, expected_suffix):
    monkeypatch.setattr(install.platform, "system", lambda: system)
    monkeypatch.setattr(install.Path, "home", lambda: tmp_path)

    assert install._config_path() == tmp_path / expected_suffix


def test_desktop_config_path_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(install.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert install._config_path() == tmp_path / "Claude/claude_desktop_config.json"


def test_installer_merges_default_server_and_backs_up(monkeypatch, tmp_path):
    config_path = tmp_path / "claude_desktop_config.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "example"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(install, "_config_path", lambda: config_path)

    assert install.main() == 0

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["mcpServers"]["existing"] == {"command": "example"}
    assert config["mcpServers"]["echr-py"] == {
        "command": sys.executable,
        "args": ["-m", "hudoc_py.mcp"],
    }
    assert len(list(tmp_path.glob("claude_desktop_config.json.bak.*"))) == 1


def test_installer_refuses_malformed_existing_config(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "claude_desktop_config.json"
    config_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(install, "_config_path", lambda: config_path)

    assert install.main() == 1
    assert config_path.read_text(encoding="utf-8") == "{not-json"
    assert "not valid JSON" in capsys.readouterr().err
