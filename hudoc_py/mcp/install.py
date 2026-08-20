"""Install the echr-py MCP server into Claude Desktop's config.

Run::

    python -m hudoc_py.mcp.install

Merges an ``mcpServers.echr-py`` entry into
``~/Library/Application Support/Claude/claude_desktop_config.json`` (macOS)
or ``%APPDATA%\\Claude\\claude_desktop_config.json`` (Windows). The entry
points at the Python executable that's running this installer – so install
inside the venv where ``echr-py[mcp]`` is installed.

The installer writes the verified ``<python> -m hudoc_py.mcp`` form and binds
the server to the environment in which ``echr-py[mcp]`` is installed.
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import shutil
import sys
from pathlib import Path


def _config_path() -> Path:
    """Return the platform-specific Claude Desktop config path."""
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.system() == "Windows":
        import os

        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    # Claude Desktop follows the XDG-style location on Linux.
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def main(name: str = "echr-py") -> int:
    cfg_path = _config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    config: dict = {}
    if cfg_path.exists():
        try:
            config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"ERROR: existing config is not valid JSON: {exc}", file=sys.stderr)
            return 1

        backup = cfg_path.with_suffix(
            f".json.bak.{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy(cfg_path, backup)
        print(f"Backed up existing config to: {backup}")

    config.setdefault("mcpServers", {})
    config["mcpServers"][name] = {
        "command": sys.executable,
        "args": ["-m", "hudoc_py.mcp"],
    }

    cfg_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote MCP server '{name}' to: {cfg_path}")
    print(f"  command: {sys.executable}")
    print("  args:    -m hudoc_py.mcp")
    print("")
    print("Quit and reopen Claude Desktop for the change to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
