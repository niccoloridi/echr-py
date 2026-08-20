"""ENG/FRE reconciliation: one primary case per ECLI, with French siblings.

Operates on a :class:`CaseCollection` and is pure: no network, no disk.

Fixes carried over from the source's known issues:

* Extra French rows under an already-matched ECLI are dropped by default
  (source left them as duplicate primaries – the "~588 duplicate primaries"
  bug); ``extra_sibling_policy="keep"`` restores the old behaviour for parity
  audits.
* One duplicate classifier (:func:`classify_ecli_cluster`) instead of the
  source's two divergent ones.
* Text provenance lives on the row (``french_itemid``) and is preserved, so it
  is never dropped by a later normalisation step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ..models.case import Case, CaseCollection
from .ecli import ClusterKind, classify_ecli_cluster, normalize_ecli

ExtraSiblingPolicy = Literal["drop", "keep"]


class ReconcileInvariantError(RuntimeError):
    """Raised when reconciliation leaves >1 primary for a normalized ECLI."""


class ReconcileStats(BaseModel):
    """Counts describing what reconciliation did (parity with the source)."""

    eng_rows: int = 0
    fre_rows: int = 0
    other_language_rows: int = 0
    eng_with_ecli: int = 0
    eng_matched_fre: int = 0
    eng_representedby_filled_from_fre: int = 0
    fre_unmatched: int = 0
    no_ecli_rows: int = 0
    same_language_duplicates_dropped: int = 0
    fre_extra_dropped: int = 0
    #: histogram of :data:`ClusterKind` over ECLI groups that had >1 row.
    clusters: dict[str, int] = Field(default_factory=dict)


@dataclass
class ReconcileResult:
    """Output of :func:`reconcile`."""

    cases: CaseCollection  # primaries: exactly one per normalized ECLI (+ no-ECLI passthrough)
    duplicates: CaseCollection = field(default_factory=CaseCollection)
    stats: ReconcileStats = field(default_factory=ReconcileStats)


def _present(value: str | None) -> bool:
    return bool(value and value.strip())


def _dedup_by_itemid(cases: list[Case], dropped: list[Case]) -> list[Case]:
    """Keep the first case per itemid; send later duplicates to ``dropped``."""
    seen: set[str] = set()
    kept: list[Case] = []
    for case in cases:
        key = case.itemid or ""
        if key and key in seen:
            dropped.append(case)
            continue
        if key:
            seen.add(key)
        kept.append(case)
    return kept


def reconcile(
    collection: list[Case],
    *,
    primary_language: str = "ENG",
    sibling_language: str = "FRE",
    extra_sibling_policy: ExtraSiblingPolicy = "drop",
    backfill_representedby: bool = True,
) -> ReconcileResult:
    """Reconcile ENG/FRE rows into one primary case per ECLI.

    Rows in a language other than ``primary_language``/``sibling_language`` pass
    through as their own primaries. Rows without an ECLI also pass through
    (they cannot be reconciled). The input is never mutated – primaries are
    ``model_copy()`` of the inputs, so ``french_itemid``/``represented_by``
    edits do not leak back.
    """
    primary_language = primary_language.upper()
    sibling_language = sibling_language.upper()

    stats = ReconcileStats()
    duplicates: list[Case] = []
    primaries: list[Case] = []

    # 1. Partition by language, preserving order.
    eng_rows: list[Case] = []
    fre_rows: list[Case] = []
    for case in collection:
        lang = (case.language or "").upper()
        if lang == primary_language:
            eng_rows.append(case)
        elif lang == sibling_language:
            fre_rows.append(case)
        else:
            stats.other_language_rows += 1
            primaries.append(case.model_copy())

    stats.eng_rows = len(eng_rows)
    stats.fre_rows = len(fre_rows)

    # 2. Intra-language dedup by itemid (HUDOC paginates the same itemid twice).
    eng_rows = _dedup_by_itemid(eng_rows, duplicates)
    fre_rows = _dedup_by_itemid(fre_rows, duplicates)
    stats.same_language_duplicates_dropped = len(duplicates)

    # 3. Group both languages by normalized ECLI. No-ECLI rows pass through.
    eng_by_ecli: dict[str, list[Case]] = {}
    for case in eng_rows:
        key = normalize_ecli(case.ecli)
        if key is None:
            stats.no_ecli_rows += 1
            primaries.append(case.model_copy())
            continue
        stats.eng_with_ecli += 1
        eng_by_ecli.setdefault(key, []).append(case)

    fre_by_ecli: dict[str, list[Case]] = {}
    for case in fre_rows:
        key = normalize_ecli(case.ecli)
        if key is None:
            stats.no_ecli_rows += 1
            primaries.append(case.model_copy())
            continue
        fre_by_ecli.setdefault(key, []).append(case)

    # 4. Cluster diagnostics over every ECLI that appears in either language.
    for key in set(eng_by_ecli) | set(fre_by_ecli):
        group = eng_by_ecli.get(key, []) + fre_by_ecli.get(key, [])
        if len(group) > 1:
            kind: ClusterKind = classify_ecli_cluster(group)
            stats.clusters[kind] = stats.clusters.get(kind, 0) + 1

    # 5. ENG-primary groups: pick a FRE sibling, attach french_itemid, backfill.
    for key, eng_group in eng_by_ecli.items():
        primary = eng_group[0].model_copy()
        # extra ENG rows sharing this ECLI are same-language duplicates
        for extra in eng_group[1:]:
            duplicates.append(extra)
            stats.same_language_duplicates_dropped += 1

        fre_group = fre_by_ecli.pop(key, [])
        if fre_group:
            sibling = _pick_sibling(fre_group)
            primary.french_itemid = sibling.itemid
            stats.eng_matched_fre += 1
            if (
                backfill_representedby
                and not _present(primary.represented_by)
                and _present(sibling.represented_by)
            ):
                primary.represented_by = sibling.represented_by
                stats.eng_representedby_filled_from_fre += 1
            # extra FRE rows in a matched group
            extras = [c for c in fre_group if c is not sibling]
            if extra_sibling_policy == "drop":
                duplicates.extend(extras)
                stats.fre_extra_dropped += len(extras)
            else:
                for extra in extras:
                    primaries.append(extra.model_copy())
        primaries.append(primary)

    # 6. FRE-only groups (no ENG row): promote one per ECLI.
    for _key, fre_group in fre_by_ecli.items():
        sibling = _pick_sibling(fre_group)
        primaries.append(sibling.model_copy())
        stats.fre_unmatched += 1
        extras = [c for c in fre_group if c is not sibling]
        if extra_sibling_policy == "drop":
            duplicates.extend(extras)
            stats.fre_extra_dropped += len(extras)
        else:
            for extra in extras:
                primaries.append(extra.model_copy())

    # 7. Invariant: at most one primary per normalized ECLI. Under the "keep"
    # parity policy we deliberately reproduce the source's duplicate-primary
    # behaviour, so the invariant is expected not to hold there.
    if extra_sibling_policy == "drop":
        _check_invariant(primaries)

    result_cases = CaseCollection(primaries)
    result_dups = CaseCollection(duplicates)
    return ReconcileResult(cases=result_cases, duplicates=result_dups, stats=stats)


def _pick_sibling(fre_group: list[Case]) -> Case:
    """Prefer the first FRE row that names a representative; else the first."""
    for case in fre_group:
        if _present(case.represented_by):
            return case
    return fre_group[0]


def _check_invariant(primaries: list[Case]) -> None:
    seen: dict[str, str] = {}
    offenders: list[str] = []
    for case in primaries:
        key = normalize_ecli(case.ecli)
        if key is None:
            continue
        if key in seen:
            offenders.append(key)
        else:
            seen[key] = case.itemid or ""
    if offenders:
        raise ReconcileInvariantError(
            f"{len(offenders)} ECLI(s) have >1 primary after reconcile: "
            f"{offenders[:5]}"
        )
