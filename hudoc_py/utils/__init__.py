"""Cross-cutting helpers: JSONL checkpointing, retry/backoff, run logging."""

from .jsonl import (
    append_jsonl,
    append_jsonl_many,
    backup_file,
    iter_jsonl,
    load_processed_ids,
    upsert_jsonl,
)
from .retry import is_rate_limit_error, with_backoff
from .runlog import RunLog, setup_logging

__all__ = [
    "iter_jsonl",
    "load_processed_ids",
    "append_jsonl",
    "append_jsonl_many",
    "upsert_jsonl",
    "backup_file",
    "is_rate_limit_error",
    "with_backoff",
    "RunLog",
    "setup_logging",
]
