"""ECHR keypoint (kpthesaurus) label ↔ ID resolution.

HUDOC stores each case's legal keywords as numeric ``kpthesaurus`` IDs
(e.g. ``350``); the human-readable labels (e.g. "(Art. 3) Prohibition of
torture") live in a separate taxonomy. This module vendors the canonical
English keyword taxonomy (587 keypoints, refreshable via
``scripts/refresh_kpthesaurus.py``) so callers can query by keyword text
instead of memorising IDs::

    search(kpthesaurus="torture")   # resolves to the matching keypoint ID(s)

The map carries the parent-ID hierarchy too, so a keyword and its sub-keywords
can be walked.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def _keypoints() -> dict[str, dict[str, str | None]]:
    """Load the vendored ``{id: {"label", "parent"}}`` keypoint map."""
    with (
        resources.files("hudoc_py.data")
        .joinpath("kpthesaurus_eng.json")
        .open("r", encoding="utf-8") as fh
    ):
        return json.load(fh)


def keypoints() -> dict[str, str]:
    """Return the full ``{id: label}`` keypoint map."""
    return {
        tid: label
        for tid, entry in _keypoints().items()
        if (label := entry.get("label")) is not None
    }


def keypoint_label(keypoint_id: str | int) -> str | None:
    """Return the English label for a keypoint ID, or ``None`` if unknown."""
    entry = _keypoints().get(str(keypoint_id).strip())
    return entry.get("label") if entry else None


def keypoint_parent(keypoint_id: str | int) -> str | None:
    """Return the parent keypoint ID, or ``None`` for a top-level keyword."""
    entry = _keypoints().get(str(keypoint_id).strip())
    return entry["parent"] if entry else None


def search_keypoints(text: str) -> list[tuple[str, str]]:
    """Return ``(id, label)`` pairs whose label contains ``text`` (case-insensitive)."""
    needle = text.strip().lower()
    if not needle:
        return []
    return [
        (tid, label)
        for tid, entry in _keypoints().items()
        if (label := entry.get("label")) is not None and needle in label.lower()
    ]


_NUMERIC = re.compile(r"^\d+$")


def resolve_keypoint(query: str | int) -> list[str]:
    """Resolve a keypoint query to a list of keypoint IDs.

    * A numeric value (``"350"`` / ``350``) is treated as an ID and returned
      unchanged – so existing ID-based calls keep working.
    * An exact label match returns that single ID.
    * Otherwise the value is a keyword substring, and every keypoint whose
      label contains it is returned (case-insensitive).

    Raises :class:`KeyError` if a non-numeric query matches no keypoint, so a
    typo doesn't silently widen the search to the whole database.
    """
    q = str(query).strip()
    if _NUMERIC.match(q):
        return [q]

    # Exact (case-insensitive) label match wins.
    lowered = q.lower()
    exact = [
        tid
        for tid, entry in _keypoints().items()
        if (label := entry.get("label")) is not None and label.lower() == lowered
    ]
    if exact:
        return exact

    matches = [tid for tid, _label in search_keypoints(q)]
    if not matches:
        raise KeyError(
            f"No ECHR keypoint matches {query!r}. Try hudoc_py.thesaurus."
            "search_keypoints(...) to find one, or pass a numeric keypoint ID."
        )
    return matches
