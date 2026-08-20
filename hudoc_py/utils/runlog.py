"""Structured run-event logging.

Every extraction run appends start / event / finish records to a JSONL
sidecar (``<output>.runs.jsonl`` by convention) so there is an auditable
record of parameters, model, provider, counts, and cost for each run.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .jsonl import append_jsonl


def setup_logging(level: int = logging.INFO) -> None:
    """Console logging format shared by the CLI commands."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunLog:
    """Append-only JSONL record of one pipeline run."""

    def __init__(self, path: str | Path, *, run_id: str | None = None):
        self.path = Path(path)
        self.run_id = run_id or uuid.uuid4().hex[:12]

    def _write(self, event: str, payload: dict[str, Any]) -> None:
        append_jsonl(self.path, {"event": event, "run_id": self.run_id, "ts": _now(), **payload})

    def start(self, **params: Any) -> None:
        self._write("start", {"params": params})

    def event(self, name: str, **fields: Any) -> None:
        self._write(name, fields)

    def finish(self, stats: dict[str, Any]) -> None:
        self._write("finish", {"stats": stats})
