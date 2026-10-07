"""Optional JSONL record of every MCP tool call.

A call log turns an assistant-driven analysis into a replayable record: each
line records the tool, its arguments, the identifiers in the response, the
package version and the time, so the same calls can be re-run through the
Python API without the model.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IDENTIFIER_KEYS: dict[str, str] = {
    "itemid": "itemids",
    "source_itemid": "itemids",
    "target_itemid": "itemids",
    "ecli": "eclis",
    "source_ecli": "eclis",
    "target_ecli": "eclis",
}
MAX_IDENTIFIERS = 1000
#: Depth of wrapped calls in flight; only the outermost (client-initiated) call is logged.
_CALL_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "echr_py_mcp_call_depth", default=0
)


def collect_identifiers(payload: Any, *, limit: int = MAX_IDENTIFIERS) -> dict[str, list[str]]:
    """Collect HUDOC item IDs and ECLIs from a JSON-like tool response, in order of appearance."""
    found: dict[str, dict[str, None]] = {"itemids": {}, "eclis": {}}
    stack: list[Any] = [payload]
    while stack:
        value = stack.pop(0)
        if isinstance(value, dict):
            for key, item in value.items():
                bucket = IDENTIFIER_KEYS.get(key)
                if bucket and isinstance(item, str) and item and len(found[bucket]) < limit:
                    found[bucket][item] = None
                elif isinstance(item, (dict, list, tuple)):
                    stack.append(item)
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif hasattr(value, "model_dump"):
            stack.append(value.model_dump(mode="json"))
    return {name: list(values) for name, values in found.items()}


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return repr(value)


class CallLogger:
    """Append one JSON line per tool call to ``path``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=_json_safe) + "\n")

    def _entry(
        self,
        name: str,
        arguments: dict[str, Any],
        started: float,
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> dict[str, Any]:
        from .. import __version__

        entry: dict[str, Any] = {
            "recorded_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "tool": name,
            "arguments": {k: _json_safe(v) for k, v in arguments.items()},
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "package_version": __version__,
        }
        if error is None:
            entry["status"] = "ok"
            entry["identifiers"] = collect_identifiers(result)
        else:
            entry["status"] = "error"
            entry["error"] = f"{type(error).__name__}: {error}"
        return entry

    def wrap(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Return ``fn`` wrapped so that every client call is recorded; signature and name are preserved.

        A tool that calls another wrapped tool internally produces one entry, for
        the outer call, so the log mirrors what the client asked for.
        """
        name = fn.__name__

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if _CALL_DEPTH.get():
                    return await fn(*args, **kwargs)
                token = _CALL_DEPTH.set(1)
                started = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as exc:
                    self.record(self._entry(name, kwargs, started, error=exc))
                    raise
                finally:
                    _CALL_DEPTH.reset(token)
                self.record(self._entry(name, kwargs, started, result=result))
                return result

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if _CALL_DEPTH.get():
                return fn(*args, **kwargs)
            token = _CALL_DEPTH.set(1)
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                self.record(self._entry(name, kwargs, started, error=exc))
                raise
            finally:
                _CALL_DEPTH.reset(token)
            self.record(self._entry(name, kwargs, started, result=result))
            return result

        return sync_wrapper


def install(server: Any, logger_: CallLogger) -> None:
    """Make ``server.tool`` register call-logged functions. Must run before any tool is registered."""
    original_tool = server.tool

    def tool(*args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        decorator = original_tool(*args, **kwargs)

        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            return decorator(logger_.wrap(fn))

        return register

    server.tool = tool


def read_call_log(path: str | Path) -> Iterable[dict[str, Any]]:
    """Yield the recorded entries of a call log."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)
