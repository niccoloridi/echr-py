"""Human-validation loop: stratified sample export + inter-rater agreement.

Generic port of cjeu-py's ``human_validation``: works over any extraction
checkpoint JSONL (one record per item with a ``data`` payload).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..utils.jsonl import iter_jsonl

logger = logging.getLogger(__name__)


def _flatten(record: dict) -> dict:
    """Flatten one checkpoint record: ``data`` keys one level up."""
    flat = {k: v for k, v in record.items() if k not in ("data", "_meta")}
    for key, value in (record.get("data") or {}).items():
        flat.setdefault(key, value)
    return flat


def export_validation_sample(
    input_path: str | Path,
    output_path: str | Path,
    *,
    sample_size: int = 200,
    stratify_by: str | None = None,
    seed: int = 42,
    columns: list[str] | None = None,
    human_fields: list[str] | None = None,
) -> Path | None:
    """Export a stratified random sample of extraction records for human review.

    Error records are excluded. ``stratify_by`` samples proportionally per
    stratum (topping up to ``sample_size``); ``human_fields`` adds empty
    ``human_<field>`` columns for the coder, plus ``human_notes``.
    """
    import pandas as pd

    records = [_flatten(r) for r in iter_jsonl(input_path) if r.get("status", "ok") == "ok"]
    if not records:
        logger.error("No valid records in %s", input_path)
        return None
    df = pd.DataFrame(records)
    logger.info("Loaded %d records from %s", len(df), input_path)

    if stratify_by and stratify_by in df.columns:
        sample = df.groupby(stratify_by, group_keys=False)[df.columns].apply(
            lambda x: x.sample(
                n=min(len(x), max(1, int(sample_size * len(x) / len(df)))),
                random_state=seed,
            )
        )
        if len(sample) < sample_size:
            remaining = df[~df.index.isin(sample.index)]
            if len(remaining):
                sample = pd.concat([
                    sample,
                    remaining.sample(
                        n=min(len(remaining), sample_size - len(sample)), random_state=seed
                    ),
                ])
    else:
        if stratify_by:
            logger.warning("stratify_by=%r not in columns; plain random sample", stratify_by)
        sample = df.sample(n=min(len(df), sample_size), random_state=seed)

    for fld in human_fields or []:
        sample[f"human_{fld}"] = ""
    sample["human_notes"] = ""

    if columns:
        keep = [c for c in columns if c in sample.columns]
        keep += [c for c in sample.columns if c.startswith("human_") and c not in keep]
        sample = sample[keep]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(output_path, index=False)
    logger.info("Exported %d records for human validation → %s", len(sample), output_path)
    return output_path


def compute_agreement(
    validation_path: str | Path,
    *,
    dimensions: list[str],
    min_coded: int = 5,
) -> dict[str, dict[str, float | int]]:
    """Cohen's kappa between the LLM columns and the ``human_*`` columns.

    Requires scikit-learn (the ``analysis`` extra). Rows without a human code
    are ignored; dimensions with fewer than ``min_coded`` coded rows are
    skipped.
    """
    import pandas as pd

    try:
        from sklearn.metrics import cohen_kappa_score
    except ImportError as exc:
        raise ImportError(
            "scikit-learn is required for compute_agreement. "
            'Install it with: pip install "echr-py[analysis]"'
        ) from exc

    df = pd.read_csv(validation_path)
    results: dict[str, dict[str, float | int]] = {}
    for dim in dimensions:
        human_col = f"human_{dim}"
        if dim not in df.columns or human_col not in df.columns:
            continue
        coded = df[df[human_col].notna() & (df[human_col].astype(str) != "")]
        if len(coded) < min_coded:
            logger.warning("Too few human codings for %s: %d", dim, len(coded))
            continue
        kappa = cohen_kappa_score(coded[dim], coded[human_col])
        results[dim] = {
            "kappa": round(float(kappa), 3),
            "n": int(len(coded)),
            "agreement_pct": round(float((coded[dim] == coded[human_col]).mean() * 100), 1),
        }
    return results
