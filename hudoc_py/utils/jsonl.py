"""Resumable JSONL checkpoint logs.

Uses a generic ``id_field`` (``"id"`` is always accepted as a fallback for
compatibility with old logs).
Every long-running pipeline writes one JSONL record per item so a crashed or
interrupted run can resume by skipping already-processed ids.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = "backups"


def iter_jsonl(path: str | Path) -> Iterator[dict]:
    """Yield records from a JSONL file, skipping blank and malformed lines."""
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSONL line %d in %s", lineno, path)


def _record_id(record: dict, id_field: str) -> str | None:
    value = record.get(id_field) or record.get("id")
    return str(value) if value else None


def load_processed_ids(
    path: str | Path,
    *,
    id_field: str = "itemid",
    ok_only: bool = False,
    status_field: str = "status",
) -> set[str]:
    """Return the set of ids already present in a checkpoint log.

    With ``ok_only=True`` only records whose ``status_field`` equals ``"ok"``
    count as processed – the resume policy behind ``--retry-errors``.
    """
    processed: set[str] = set()
    for record in iter_jsonl(path):
        if ok_only and record.get(status_field) != "ok":
            continue
        rid = _record_id(record, id_field)
        if rid:
            processed.add(rid)
    if processed:
        logger.info("Loaded %d processed ids from %s", len(processed), path)
    return processed


def append_jsonl(path: str | Path, record: dict) -> None:
    """Append a single record to a JSONL file, creating parents as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def append_jsonl_many(path: str | Path, records: list[dict]) -> None:
    """Append a batch of records to a JSONL file."""
    if not records:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in records) + "\n")


def upsert_jsonl(path: str | Path, records: list[dict], *, id_field: str = "itemid") -> None:
    """Upsert records into a JSONL log by id, preserving existing order.

    Existing rows with a matching id are replaced in place; new ids are
    appended. Records without an id are appended as-is.
    """
    if not records:
        return
    path = Path(path)

    existing: list[dict] = []
    positions: dict[str, int] = {}
    for record in iter_jsonl(path):
        rid = _record_id(record, id_field)
        if rid:
            positions[rid] = len(existing)
        existing.append(record)

    for record in records:
        rid = _record_id(record, id_field)
        if rid is None:
            existing.append(record)
        elif rid in positions:
            existing[positions[rid]] = record
        else:
            positions[rid] = len(existing)
            existing.append(record)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in existing:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def backup_file(
    path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    max_backups: int = 10,
    min_size_bytes: int = 100,
) -> Path | None:
    """Create a timestamped backup of a file before modifying it.

    Skips missing or tiny files. Keeps at most ``max_backups`` backups per
    file (oldest deleted first). Returns the backup path, or ``None`` if no
    backup was made.
    """
    path = Path(path)
    if not path.exists():
        return None
    size = path.stat().st_size
    if size < min_size_bytes:
        logger.debug("Skipping backup of %s (size %d < %d bytes)", path, size, min_size_bytes)
        return None

    backup_root = Path(backup_dir) if backup_dir else path.parent / DEFAULT_BACKUP_DIR
    backup_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_root / f"{path.stem}_{timestamp}{path.suffix}"
    try:
        shutil.copy2(path, backup_path)
        logger.info("Created backup: %s (%s bytes)", backup_path, f"{size:,}")
    except OSError as exc:
        logger.warning("Failed to back up %s: %s", path, exc)
        return None

    _cleanup_old_backups(backup_root, path.stem, path.suffix, max_backups)
    return backup_path


def _cleanup_old_backups(backup_root: Path, stem: str, suffix: str, max_backups: int) -> None:
    try:
        backups = sorted(
            (p for p in backup_root.glob(f"{stem}_*{suffix}") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for oldest in backups[: max(0, len(backups) - max_backups)]:
            try:
                oldest.unlink()
                logger.debug("Removed old backup: %s", oldest)
            except OSError as exc:
                logger.warning("Failed to remove old backup %s: %s", oldest, exc)
    except OSError as exc:
        logger.warning("Error cleaning up backups in %s: %s", backup_root, exc)
