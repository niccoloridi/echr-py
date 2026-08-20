"""Tests for the optional Streamlit GUI wiring (no Streamlit runtime needed)."""

from __future__ import annotations

import sys

import pytest


def test_gui_module_imports_without_streamlit():
    # The package must import even when streamlit is not installed.
    import hudoc_py.gui as gui

    assert gui.APP_PATH.name == "app.py"
    assert gui.APP_PATH.exists()


def test_launch_raises_helpful_error_without_streamlit(monkeypatch):
    import hudoc_py.gui as gui

    # Simulate streamlit being absent.
    monkeypatch.setitem(sys.modules, "streamlit", None)
    with pytest.raises(ImportError, match="echr-py\\[gui\\]"):
        gui.launch()


def test_cli_gui_command_parses():
    from hudoc_py.cli import build_parser

    args = build_parser().parse_args(["gui"])
    assert args.command == "gui"
