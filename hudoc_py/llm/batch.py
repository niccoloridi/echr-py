"""Gemini Batch API workflow – four phases, generic over extractors.

The Batch API charges roughly half the realtime price, at the cost of an
asynchronous job lifecycle. The transport is domain-neutral: callers produce
:class:`PreparedRequest` objects and a ``parse_payload`` callback.

Phases::

    prepare_batch(requests, ...)      # write requests JSONL + provenance CSV
    submit_batch(...)                 # upload file, create job, write status JSON
    poll_batch(wait=True, ...)        # refresh status until a terminal state
    retrieve_batch(parse_payload=..., ...)   # download + parse into records

Artifacts (all paths are caller-chosen):

* **requests JSONL** – one ``{"key": ..., "request": {...}}`` per line in the
  Batch API's GenerateContentRequest format, schema-constrained when the
  request carries a ``response_schema``.
* **provenance CSV** – ``key, source_itemid, source_language, word_count,
  word_limit, extractor``; consumed at retrieve time to stamp provenance
  onto records.
* **status JSON** – job name, uploaded file, model, state, result file.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from .base import estimate_cost_for
from .client import _import_genai, get_gemini_client

logger = logging.getLogger(__name__)

TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}

PROVENANCE_FIELDS = (
    "key", "source_itemid", "source_language", "word_count", "word_limit", "extractor",
)


@dataclass
class PreparedRequest:
    """One extraction request, produced by an extractor's ``prepare()``."""

    key: str
    user_text: str
    system_instruction: str
    response_schema: dict | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


def retry_artifact_path(path: str | Path) -> Path:
    """Sibling path for expanded-retry artifacts: ``x.jsonl`` → ``x.retry.jsonl``."""
    path = Path(path)
    return path.with_name(f"{path.stem}.retry{path.suffix}")


# Keys the Batch API's Schema proto accepts. Anything else (``$ref``/``$defs``
# from Pydantic's model_json_schema, ``title``, ``default``, ...) is rejected
# with a 400 "no such field" – verified against the live API.
_SCHEMA_ALLOWED_KEYS = {
    "type", "format", "description", "nullable", "enum", "items",
    "properties", "required", "minimum", "maximum", "minItems", "maxItems",
    "anyOf",
}


def sanitise_response_schema(
    schema: dict[str, Any], *, require_all: bool = True
) -> dict[str, Any]:
    """Make a JSON schema acceptable to the Batch API's Schema proto.

    Dereferences ``$ref`` pointers into their ``$defs`` bodies (cycles fall
    back to a plain object) and strips unsupported keywords.

    ``require_all`` marks every property required at every object level.
    Extraction schemas can keep fields optional for lenient *parsing*, but in
    batch mode a constrained decoder may emit only one optional field and
    stop. Requiring the fields in the request forces complete output; parsing
    stays lenient downstream.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any, seen: tuple[str, ...] = ()) -> Any:
        if isinstance(node, list):
            return [walk(v, seen) for v in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.split("/")[-1]
            if name in seen or name not in defs:
                return {"type": "object"}  # cycle or dangling ref
            return walk(defs[name], (*seen, name))
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key not in _SCHEMA_ALLOWED_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                # Keys here are arbitrary property names, not schema keywords.
                out[key] = {prop: walk(spec, seen) for prop, spec in value.items()}
            elif key in ("required", "enum"):
                out[key] = value
            else:
                out[key] = walk(value, seen)
        if require_all and isinstance(out.get("properties"), dict):
            out["required"] = list(out["properties"])
        return out

    return walk(schema)


# --- Phase 1: prepare ---------------------------------------------------------


def prepare_batch(
    requests: Iterable[PreparedRequest],
    *,
    requests_path: str | Path,
    provenance_path: str | Path,
    temperature: float = 0.0,
    thinking_budget: int = 0,
) -> int:
    """Write batch requests JSONL + provenance side-CSV. Returns request count.

    ``thinking_budget`` defaults to 0 to match the realtime path – leaving it
    unset lets gemini-2.5-flash think dynamically, which in live testing
    burned ~9k thinking tokens per document and then produced near-empty
    JSON under schema-constrained decoding.
    """
    requests_path = Path(requests_path)
    provenance_path = Path(provenance_path)
    requests_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with requests_path.open("w", encoding="utf-8") as rf, \
            provenance_path.open("w", encoding="utf-8", newline="") as pf:
        writer = csv.DictWriter(pf, fieldnames=list(PROVENANCE_FIELDS), extrasaction="ignore")
        writer.writeheader()
        for req in requests:
            generation_config: dict[str, Any] = {
                "temperature": temperature,
                "response_mime_type": "application/json",
                "thinking_config": {"thinking_budget": thinking_budget},
            }
            if req.response_schema is not None:
                generation_config["response_schema"] = sanitise_response_schema(
                    req.response_schema
                )
            line = {
                "key": req.key,
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": req.user_text}]}],
                    "generation_config": generation_config,
                    "system_instruction": {"parts": [{"text": req.system_instruction}]},
                },
            }
            rf.write(json.dumps(line, ensure_ascii=False) + "\n")
            writer.writerow({"key": req.key, **req.provenance})
            count += 1

    logger.info("Wrote %d batch requests to %s (+ provenance %s)", count, requests_path, provenance_path)
    return count


# --- Phase 2: submit ---------------------------------------------------------


def submit_batch(
    *,
    requests_path: str | Path,
    status_path: str | Path,
    display_name: str,
    model: str | None = None,
) -> str | None:
    """Upload the requests file and create the batch job. Returns the job name."""
    requests_path = Path(requests_path)
    if not requests_path.exists():
        logger.error("Requests file not found: %s", requests_path)
        return None

    _, types = _import_genai()
    client = get_gemini_client()
    model = model or config.GEMINI_BATCH_MODEL

    logger.info("Uploading %s to the Gemini File API...", requests_path)
    uploaded_file = client.files.upload(
        file=str(requests_path),
        config=types.UploadFileConfig(display_name=display_name, mime_type="jsonl"),
    )
    logger.info("Creating batch job (model %s)...", model)
    batch_job = client.batches.create(
        model=model,
        src=uploaded_file.name,
        config={"display_name": display_name},
    )
    logger.info("Created batch job: %s", batch_job.name)

    _write_status(status_path, {
        "job_name": batch_job.name,
        "uploaded_file": uploaded_file.name,
        "model": model,
        "display_name": display_name,
        "requests_file": str(requests_path),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": "SUBMITTED",
    })
    return batch_job.name


def _write_status(status_path: str | Path, status: dict[str, Any]) -> None:
    status_path = Path(status_path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_status(status_path: str | Path) -> dict[str, Any]:
    status_path = Path(status_path)
    if not status_path.exists():
        raise FileNotFoundError(f"Batch status file not found: {status_path}")
    return json.loads(status_path.read_text(encoding="utf-8"))


# --- Phase 3: poll ------------------------------------------------------------


def poll_batch(
    *,
    status_path: str | Path,
    wait: bool = False,
    poll_interval: int = 60,
    max_polls: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Refresh (and persist) job status; with ``wait=True`` block until terminal."""
    status = _read_status(status_path)
    job_name = status.get("job_name")
    if not job_name:
        status["error"] = "No job_name in status file"
        return status

    client = get_gemini_client()
    polls = 0
    while True:
        batch_job = client.batches.get(name=job_name)
        state = batch_job.state.name
        status["state"] = state
        status["last_polled"] = time.strftime("%Y-%m-%d %H:%M:%S")
        dest = getattr(batch_job, "dest", None)
        if dest is not None and getattr(dest, "file_name", None):
            status["result_file"] = dest.file_name
        if getattr(batch_job, "error", None):
            status["error"] = str(batch_job.error)
        _write_status(status_path, status)
        logger.info("Job %s: %s", job_name, state)

        if state in TERMINAL_STATES or not wait:
            return status
        polls += 1
        if max_polls is not None and polls >= max_polls:
            logger.info("Reached max polls (%d)", max_polls)
            return status
        sleep(poll_interval)


# --- Phase 4: retrieve --------------------------------------------------------


def load_provenance(provenance_path: str | Path) -> dict[str, dict[str, str]]:
    """Load the provenance side-CSV keyed by request key."""
    provenance_path = Path(provenance_path)
    if not provenance_path.exists():
        logger.warning(
            "No provenance side-file at %s – records will lack source_itemid/"
            "source_language. Was prepare_batch run with this path?", provenance_path,
        )
        return {}
    with provenance_path.open("r", encoding="utf-8") as fh:
        return {row["key"]: dict(row) for row in csv.DictReader(fh)}


def _usage_meta(parsed: dict[str, Any], model: str) -> dict[str, Any]:
    """Extract token usage from a batch result line when present."""
    response = parsed.get("response") or {}
    usage = response.get("usageMetadata") or {}
    input_tokens = int(usage.get("promptTokenCount", 0) or 0)
    output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
    thinking_tokens = int(usage.get("thoughtsTokenCount", 0) or 0)
    meta: dict[str, Any] = {
        "source": "batch",
        "provider": "gemini",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "cost_usd": estimate_cost_for(
            model, input_tokens, output_tokens, thinking_tokens, batch=True
        ),
    }
    if not usage:
        meta["note"] = "Token usage not present in batch result line"
    return meta


def extract_response_payload(parsed: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Pull the JSON payload out of one batch result line.

    Returns ``(data, error)`` – exactly one is non-None.
    """
    if parsed.get("error"):
        return None, str(parsed["error"])
    response = parsed.get("response")
    if not response:
        return None, "No response in batch result"
    try:
        text = response["candidates"][0]["content"]["parts"][0].get("text", "")
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        return None, f"Malformed response: {exc}"
    if not text:
        return None, "Empty response text"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON response: {exc}"
    if not isinstance(data, dict):
        return None, "JSON response is not an object"
    return data, None


def retrieve_batch(
    *,
    status_path: str | Path,
    provenance_path: str | Path,
    output_path: str | Path,
    parse_payload: Callable[[dict[str, Any], dict[str, str]], dict[str, Any]],
    upsert: bool = False,
    id_field: str = "itemid",
) -> int:
    """Download the result file and write one record per request to ``output_path``.

    ``parse_payload(parsed_line, provenance_row)`` is the extractor-specific
    hook mapping one raw batch result line (plus its provenance) to a
    checkpoint record. With ``upsert=True`` records replace same-id rows in an
    existing output file (used by expanded-retry batches).
    """
    from ..utils.jsonl import append_jsonl_many, upsert_jsonl

    status = _read_status(status_path)
    state = status.get("state")
    if state != "JOB_STATE_SUCCEEDED":
        logger.error("Job not in JOB_STATE_SUCCEEDED (state=%s); poll first", state)
        return 0
    result_file = status.get("result_file")
    if not result_file:
        logger.error("No result_file recorded in %s", status_path)
        return 0

    client = get_gemini_client()
    logger.info("Downloading batch results from %s...", result_file)
    content = client.files.download(file=result_file)
    text = content.decode("utf-8") if isinstance(content, bytes) else str(content)

    provenance = load_provenance(provenance_path)
    model = status.get("model", config.GEMINI_BATCH_MODEL)

    records: list[dict[str, Any]] = []
    errors = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed batch result line")
            errors += 1
            continue
        prov_row = provenance.get(str(parsed.get("key", "") or ""), {})
        record = parse_payload(parsed, prov_row)
        record.setdefault("_meta", {}).update(_usage_meta(parsed, model))
        records.append(record)
        if record.get("status") == "error":
            errors += 1

    if upsert:
        upsert_jsonl(output_path, records, id_field=id_field)
    else:
        append_jsonl_many(output_path, records)

    status["retrieved"] = True
    status["result_count"] = len(records)
    status["error_count"] = errors
    _write_status(status_path, status)
    logger.info("Wrote %d records (%d errors) to %s", len(records), errors, output_path)
    return len(records)


# --- Job management -------------------------------------------------------------


def list_batch_jobs(limit: int = 10) -> list[dict[str, Any]]:
    """List recent batch jobs (name, state, display name, created)."""
    client = get_gemini_client()
    jobs = client.batches.list(config={"page_size": limit})
    out = []
    for job in jobs:
        out.append({
            "name": job.name,
            "state": job.state.name if getattr(job, "state", None) else "?",
            "display_name": getattr(job, "display_name", ""),
            "create_time": str(getattr(job, "create_time", "")),
        })
        if len(out) >= limit:
            break
    return out


def cancel_batch_job(
    *,
    status_path: str | Path | None = None,
    job_name: str | None = None,
) -> bool:
    """Cancel a batch job by explicit name or from a status file."""
    if job_name is None:
        if status_path is None:
            raise ValueError("Provide job_name or status_path")
        job_name = _read_status(status_path).get("job_name")
        if not job_name:
            logger.error("No job_name in %s", status_path)
            return False
    client = get_gemini_client()
    client.batches.cancel(name=job_name)
    logger.info("Cancelled batch job %s", job_name)
    if status_path is not None:
        status = _read_status(status_path)
        status["state"] = "JOB_STATE_CANCELLED"
        _write_status(status_path, status)
    return True
