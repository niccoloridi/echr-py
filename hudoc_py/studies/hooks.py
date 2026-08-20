"""Extension hooks for application-owned research studies."""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StudyHook(Protocol):
    def prepare_unit(self, unit: dict[str, Any]) -> dict[str, Any] | None: ...
    def validate_record(
        self, data: dict[str, Any], unit: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]: ...


def load_study_hook(name: str, *, installed_only: bool = False) -> StudyHook:
    matches = {ep.name: ep for ep in entry_points(group="hudoc_py.studies")}
    if name in matches:
        value = matches[name].load()
    elif installed_only:
        raise ValueError(f"study hook {name!r} is not an installed hudoc_py.studies entry point")
    else:
        if ":" not in name:
            raise ValueError("hook must be an installed entry point or 'module:object'")
        module, attr = name.split(":", 1)
        value = getattr(importlib.import_module(module), attr)
    hook = value() if isinstance(value, type) else value
    if not isinstance(hook, StudyHook):
        raise TypeError(f"study hook {name!r} does not implement StudyHook")
    return hook


__all__ = ["StudyHook", "load_study_hook"]
