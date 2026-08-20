"""Portable, deterministic ``hudoc-corpus/v1`` bundles.

The bundle contract is intentionally independent of any particular published
dataset.  It describes files produced by :func:`build_corpus`, verifies their
checksums and core cross-table references, and creates a byte-reproducible ZIP.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models import Sections
from ..text import extract_dispositive_paragraphs
from ..utils.jsonl import iter_jsonl

SCHEMA_VERSION = "hudoc-corpus/v1"
_MANIFEST = "corpus-manifest.json"
_EXCLUDED_NAMES = {_MANIFEST, "validation-report.json", "package-report.json"}
_CONTAMINANT_NAMES = {".DS_Store", ".env"}
_CONTAMINANT_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


class CorpusFile(BaseModel):
    """One checksummed file in a portable corpus."""

    path: str
    bytes: int
    sha256: str


class CorpusManifest(BaseModel):
    """Stable inventory for a ``hudoc-corpus/v1`` directory."""

    schema_version: Literal["hudoc-corpus/v1"] = "hudoc-corpus/v1"
    files: list[CorpusFile] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """A typed validation finding."""

    code: str
    severity: Literal["error", "warning"] = "error"
    message: str
    path: str | None = None


class CorpusValidationReport(BaseModel):
    """Result of validating a corpus directory."""

    schema_version: Literal["hudoc-corpus-validation/v1"] = "hudoc-corpus-validation/v1"
    corpus_schema_version: str | None = None
    valid: bool = False
    files_checked: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)


class CorpusPackageReport(BaseModel):
    """Description of a deterministic corpus archive."""

    schema_version: Literal["hudoc-corpus-package/v1"] = "hudoc-corpus-package/v1"
    archive: str
    bytes: int
    sha256: str
    files: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_paths(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.name not in _EXCLUDED_NAMES
            and path.name not in _CONTAMINANT_NAMES
            and path.suffix.lower() not in {".zip", ".pyc"}
            and not (_CONTAMINANT_PARTS & set(path.parts))
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def generate_corpus_manifest(corpus_dir: str | Path) -> CorpusManifest:
    """Inventory corpus files in stable path order and write the manifest."""

    root = Path(corpus_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Corpus directory does not exist: {root}")
    manifest = CorpusManifest(
        files=[
            CorpusFile(
                path=path.relative_to(root).as_posix(),
                bytes=path.stat().st_size,
                sha256=_sha256(path),
            )
            for path in _bundle_paths(root)
        ]
    )
    (root / _MANIFEST).write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def _read_ids(path: Path, column: str) -> set[str]:
    if not path.exists():
        return set()
    import pandas as pd

    frame = pd.read_parquet(path, columns=[column])
    return {str(value) for value in frame[column].dropna().tolist()}


def validate_corpus(
    corpus_dir: str | Path,
    *,
    out: str | Path | None = None,
) -> CorpusValidationReport:
    """Validate checksums, required artifacts, and rich-table ownership."""

    root = Path(corpus_dir)
    issues: list[ValidationIssue] = []
    manifest_path = root / _MANIFEST
    if not manifest_path.exists():
        issues.append(ValidationIssue(code="missing_manifest", message=f"Missing {_MANIFEST}"))
        manifest = None
    else:
        try:
            manifest = CorpusManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            issues.append(
                ValidationIssue(code="invalid_manifest", message=str(exc), path=_MANIFEST)
            )
            manifest = None

    for required in ("cases.parquet", "report.json"):
        if not (root / required).is_file():
            issues.append(
                ValidationIssue(
                    code="missing_required_file",
                    message=f"Required artifact is absent: {required}",
                    path=required,
                )
            )

    if manifest is not None:
        expected_paths = {entry.path for entry in manifest.files}
        actual_paths = {path.relative_to(root).as_posix() for path in _bundle_paths(root)}
        for missing in sorted(expected_paths - actual_paths):
            issues.append(
                ValidationIssue(
                    code="missing_file", message="Manifest file is absent", path=missing
                )
            )
        for extra in sorted(actual_paths - expected_paths):
            issues.append(
                ValidationIssue(
                    code="unmanifested_file",
                    message="File is not recorded in the manifest",
                    path=extra,
                )
            )
        for entry in manifest.files:
            path = root / entry.path
            if not path.is_file():
                continue
            if path.stat().st_size != entry.bytes or _sha256(path) != entry.sha256:
                issues.append(
                    ValidationIssue(
                        code="checksum_mismatch",
                        message="Size or SHA-256 does not match the manifest",
                        path=entry.path,
                    )
                )

    # Rich tables all carry itemid; references must lead to a corpus case.
    try:
        canonical_ids = _read_ids(root / "cases.parquet", "itemid")
        language_versions = root / "language-versions.parquet"
        language_ids = _read_ids(language_versions, "itemid")
        if language_versions.exists():
            import pandas as pd

            frame = pd.read_parquet(language_versions, columns=["primary_itemid"])
            primary_ids = {str(value) for value in frame["primary_itemid"].dropna()}
            orphaned_primaries = primary_ids - canonical_ids
            if orphaned_primaries:
                issues.append(
                    ValidationIssue(
                        code="orphaned_primary_itemid",
                        message=(
                            f"{len(orphaned_primaries)} language versions refer to a primary "
                            "outside cases.parquet"
                        ),
                        path="language-versions.parquet",
                    )
                )
        case_ids = canonical_ids | language_ids
        for name in (
            "paragraphs.parquet",
            "sections.parquet",
            "opinions.parquet",
            "bench.parquet",
            "judges.parquet",
            "footnotes.parquet",
            "dispositive.parquet",
        ):
            path = root / name
            if not path.exists():
                continue
            orphaned = _read_ids(path, "itemid") - case_ids
            if orphaned:
                issues.append(
                    ValidationIssue(
                        code="orphaned_itemid",
                        message=f"{len(orphaned)} item IDs do not exist in cases.parquet",
                        path=name,
                    )
                )
    except Exception as exc:  # malformed parquet is a validation result, not a crash
        issues.append(ValidationIssue(code="table_read_error", message=str(exc)))

    report = CorpusValidationReport(
        corpus_schema_version=manifest.schema_version if manifest else None,
        valid=not any(issue.severity == "error" for issue in issues),
        files_checked=len(manifest.files) if manifest else 0,
        issues=issues,
    )
    if out is not None:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report


def package_corpus(corpus_dir: str | Path, out: str | Path) -> CorpusPackageReport:
    """Validate and write a byte-reproducible ZIP of a corpus directory."""

    root = Path(corpus_dir)
    if (root / _MANIFEST).exists():
        existing = validate_corpus(root)
        if not existing.valid:
            summary = "; ".join(issue.code for issue in existing.issues[:5])
            raise ValueError(f"Refusing to re-manifest an invalid corpus: {summary}")
        manifest = CorpusManifest.model_validate_json(
            (root / _MANIFEST).read_text(encoding="utf-8")
        )
    else:
        manifest = generate_corpus_manifest(root)
    validation = validate_corpus(root)
    if not validation.valid:
        summary = "; ".join(issue.code for issue in validation.issues[:5])
        raise ValueError(f"Refusing to package invalid corpus: {summary}")

    destination = Path(out)
    if destination.suffix.lower() != ".zip":
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination / "hudoc-corpus-v1.zip"
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)

    files = [root / entry.path for entry in manifest.files] + [root / _MANIFEST]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(files, key=lambda value: value.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with path.open("rb") as source, zf.open(info, "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)

    return CorpusPackageReport(
        archive=str(destination),
        bytes=destination.stat().st_size,
        sha256=_sha256(destination),
        files=len(files),
    )


def write_rich_section_tables(
    corpus_dir: str | Path,
    *,
    sections_path: str | Path | None = None,
) -> dict[str, int]:
    """Flatten ``sections.jsonl`` into neutral Parquet tables."""

    import pandas as pd

    root = Path(corpus_dir)
    tables: dict[str, list[dict[str, Any]]] = {
        "paragraphs": [],
        "sections": [],
        "opinions": [],
        "bench": [],
        "judges": [],
        "footnotes": [],
        "dispositive": [],
    }
    source = Path(sections_path) if sections_path is not None else root / "sections.jsonl"
    for record in iter_jsonl(source):
        itemid = str(record.get("itemid") or "")
        language = record.get("source_language")
        sections = Sections.model_validate(record["sections"])
        spine = sections.spine
        if spine:
            for block in spine.blocks:
                if block.type in {"paragraph", "list_item", "blockquote", "footnote"}:
                    tables["paragraphs"].append(
                        {
                            "itemid": itemid,
                            "language": language,
                            **block.model_dump(mode="json"),
                        }
                    )
            for footnote in spine.footnotes:
                tables["footnotes"].append(
                    {
                        "itemid": itemid,
                        "language": language,
                        **footnote.model_dump(mode="json"),
                    }
                )
        for span in sections.spans:
            tables["sections"].append(
                {"itemid": itemid, "language": language, **span.model_dump(mode="json")}
            )
        for opinion in sections.opinions:
            tables["opinions"].append(
                {"itemid": itemid, "language": language, **opinion.model_dump(mode="json")}
            )
        if sections.bench:
            tables["bench"].append(
                {
                    "itemid": itemid,
                    "language": language,
                    **sections.bench.model_dump(mode="json"),
                }
            )
            for ordinal, member in enumerate(sections.bench.members, 1):
                tables["judges"].append(
                    {
                        "itemid": itemid,
                        "language": language,
                        "ordinal": ordinal,
                        **member.model_dump(mode="json"),
                    }
                )
        for ruling in extract_dispositive_paragraphs(sections):
            tables["dispositive"].append(
                {"itemid": itemid, "language": language, **ruling.model_dump(mode="json")}
            )

    def parquet_safe(row: dict[str, Any]) -> dict[str, Any]:
        # Portable Parquet cannot represent an empty struct (and nested model
        # shapes evolve). Preserve such values as canonical JSON columns.
        return {
            key: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if isinstance(value, (dict, list))
            else value
            for key, value in row.items()
        }

    counts: dict[str, int] = {}
    for name, rows in tables.items():
        counts[name] = len(rows)
        # Always create the declared table, even when empty.
        safe_rows = [parquet_safe(row) for row in rows]
        frame = pd.DataFrame(safe_rows if safe_rows else [{"itemid": None, "language": None}]).iloc[
            : len(rows)
        ]
        frame.to_parquet(root / f"{name}.parquet", index=False)
    return counts


__all__ = [
    "CorpusFile",
    "CorpusManifest",
    "CorpusPackageReport",
    "CorpusValidationReport",
    "ValidationIssue",
    "generate_corpus_manifest",
    "package_corpus",
    "validate_corpus",
    "write_rich_section_tables",
]
