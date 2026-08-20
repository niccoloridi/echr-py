"""Optional Streamlit dashboard (install the ``gui`` extra)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")


def launch() -> int:
    """Launch the Streamlit dashboard via ``streamlit run``.

    Returns the subprocess exit code. Raises a helpful error if Streamlit is
    not installed.
    """
    try:
        import streamlit  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Streamlit is required for the GUI. Install with: pip install 'echr-py[gui]'"
        ) from exc
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(APP_PATH)])


__all__ = ["launch", "APP_PATH"]
