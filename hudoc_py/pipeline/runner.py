"""Generic concurrent extraction runner: checkpoint, throttle, cost meter.

Deliberately synchronous (ThreadPool): the LLM SDKs are blocking, extraction
runs are batch CLI workloads, and the throttled-submission pattern is shared
with the related cjeu-py project. The package's async core stays reserved for
HUDOC HTTP.

The ``worker`` returns one checkpoint record per item (usually
``ExtractionRecord.to_log_record()``); records land on disk as they complete,
so a crashed run resumes by skipping ids already in the output file.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..utils.jsonl import append_jsonl, backup_file, load_processed_ids
from ..utils.runlog import RunLog

logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    total: int = 0
    processed: int = 0
    ok: int = 0
    errors: int = 0
    skipped: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {k: round(v, 6) if isinstance(v, float) else v for k, v in self.__dict__.items()}


@dataclass
class _Meter:
    stats: PipelineStats = field(default_factory=PipelineStats)

    def add(self, record: dict[str, Any]) -> None:
        self.stats.processed += 1
        if record.get("status") == "ok":
            self.stats.ok += 1
        else:
            self.stats.errors += 1
        meta = record.get("_meta") or {}
        self.stats.input_tokens += int(meta.get("input_tokens") or 0)
        self.stats.output_tokens += int(meta.get("output_tokens") or 0)
        self.stats.thinking_tokens += int(meta.get("thinking_tokens") or 0)
        self.stats.cost_usd += float(meta.get("cost_usd") or 0.0)


def _default_get_id(id_field: str) -> Callable[[Any], str]:
    def get_id(item: Any) -> str:
        if isinstance(item, dict):
            value = item.get(id_field) or item.get("id")
        else:
            value = getattr(item, id_field, None)
        return str(value) if value else ""

    return get_id


def run_pipeline(
    items: Iterable[Any],
    worker: Callable[[Any], dict[str, Any]],
    *,
    output_path: str | Path,
    id_field: str = "itemid",
    get_id: Callable[[Any], str] | None = None,
    max_workers: int | None = None,
    submit_delay: float | None = None,
    max_items: int | None = None,
    resume: bool = True,
    retry_errors: bool = False,
    backup: bool = True,
    run_log: RunLog | None = None,
    progress: bool = True,
) -> PipelineStats:
    """Map ``worker`` over ``items`` concurrently with checkpoint/resume.

    * ``resume=True`` skips items whose id is already in ``output_path``
      (``retry_errors=True`` re-runs items whose last record was an error).
    * Submission is throttled by ``submit_delay`` seconds per task.
    * A worker exception yields an error record instead of killing the run.
    """
    output_path = Path(output_path)
    get_id = get_id or _default_get_id(id_field)
    max_workers = max_workers or config.GEMINI_MAX_WORKERS
    submit_delay = config.GEMINI_SUBMIT_DELAY if submit_delay is None else submit_delay

    meter = _Meter()
    t0 = time.time()

    processed_ids: set[str] = set()
    if resume:
        processed_ids = load_processed_ids(
            output_path, id_field=id_field, ok_only=retry_errors
        )
    if backup and processed_ids:
        backup_file(output_path)

    todo: list[Any] = []
    for item in items:
        item_id = get_id(item)
        if resume and item_id and item_id in processed_ids:
            meter.stats.skipped += 1
            continue
        todo.append(item)
        if max_items is not None and len(todo) >= max_items:
            break
    meter.stats.total = len(todo)

    if run_log is not None:
        run_log.start(
            output=str(output_path), total=len(todo), skipped=meter.stats.skipped,
            max_workers=max_workers, resume=resume, retry_errors=retry_errors,
        )
    if not todo:
        logger.info("Nothing to do (%d already processed)", meter.stats.skipped)
        meter.stats.duration_s = time.time() - t0
        if run_log is not None:
            run_log.finish(meter.stats.as_dict())
        return meter.stats

    bar = None
    if progress:
        try:
            from tqdm import tqdm

            bar = tqdm(total=len(todo), unit="doc")
        except ImportError:
            pass

    def _wrapped(item: Any) -> dict[str, Any]:
        item_id = get_id(item)
        try:
            return worker(item)
        except Exception as exc:  # noqa: BLE001 – one bad item must not kill the run
            logger.error("Worker failed for %s: %s", item_id, exc)
            return {id_field: item_id, "status": "error", "_meta": {"error": str(exc)}}

    def _collect(done: set[Future]) -> None:
        for future in done:
            record = future.result()
            append_jsonl(output_path, record)
            meter.add(record)
            if bar is not None:
                bar.update(1)
                bar.set_postfix(
                    ok=meter.stats.ok, err=meter.stats.errors,
                    cost=f"${meter.stats.cost_usd:.2f}",
                )

    pending: set[Future] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for item in todo:
            pending.add(pool.submit(_wrapped, item))
            if submit_delay:
                time.sleep(submit_delay)
            if len(pending) >= max_workers * 2:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                _collect(done)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            _collect(done)

    if bar is not None:
        bar.close()

    meter.stats.duration_s = round(time.time() - t0, 2)
    logger.info(
        "Pipeline done: %d ok, %d errors, %d skipped – $%.4f in %.1fs",
        meter.stats.ok, meter.stats.errors, meter.stats.skipped,
        meter.stats.cost_usd, meter.stats.duration_s,
    )
    if run_log is not None:
        run_log.finish(meter.stats.as_dict())
    return meter.stats


def estimate_run_cost(
    items: list[Any],
    prepare: Callable[[Any], Any],
    provider: Any,
    *,
    sample_size: int = 5,
    expected_output_tokens: int = 500,
    model: str | None = None,
) -> dict[str, Any]:
    """Token-count the first N prompts and extrapolate a whole-run cost.

    ``prepare`` maps an item to a PreparedRequest (or None to skip). Used by
    the CLI's ``--dry-run``.
    """
    from ..llm.base import estimate_cost_for

    sample = []
    for item in items[: sample_size * 3]:
        request = prepare(item)
        if request is not None:
            sample.append(request)
        if len(sample) >= sample_size:
            break
    if not sample:
        return {"items": len(items), "estimated_cost_usd": 0.0, "note": "no text found in sample"}

    model = model or config.GEMINI_MODEL
    token_counts = [
        provider.count_tokens(req.system_instruction + "\n" + req.user_text) for req in sample
    ]
    avg_input = sum(token_counts) / len(token_counts)
    per_item = estimate_cost_for(model, int(avg_input), expected_output_tokens)
    return {
        "items": len(items),
        "sampled": len(sample),
        "avg_input_tokens": int(avg_input),
        "expected_output_tokens": expected_output_tokens,
        "model": model,
        "estimated_cost_usd": round(per_item * len(items), 4),
    }
