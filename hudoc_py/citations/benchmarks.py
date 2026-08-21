"""Optional import and comparison helpers for third-party citation exports."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import re
import unicodedata
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, cast
from xml.etree import ElementTree as ET

from .reporter import APPNO_REGEX, normalize_reference_key

BENCHMARK_SOURCES: dict[str, dict[str, Any]] = {
    "mumford": {
        "repository": "https://github.com/jamumford/ECHR-citation-context-v1",
        "revision": "166b15c31276bfa8c8775e7e7def575e916aced7",
        "license": "CC0-1.0",
        "archive_sha256": "665c7606baa0ff3c37ff193daee362b83733b832ea0d957d65f5aca74a1e444e",
    },
    "ecthr-pcr": {
        "repository": "https://github.com/TUMLegalTech/ECHR-PCR",
        "revision": "8c95c9aa537f9acbd475b9c34f79e1de46285d0c",
        "license": None,
        "license_status": "not declared in the pinned repository or dataset card",
        "dataset": "https://huggingface.co/datasets/RashidHaddad/ECTHR-PCR",
        "archive_sha256": "66db104c789fe26a5908f59fa91dac81ab9e29659beb62f9a87fa3156cb40ef5",
    },
}

DEFAULT_DOWNLOAD_TIMEOUT = 60.0
DEFAULT_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024

_SOURCE_FIELDS = (
    "source_itemid", "source_id", "source", "citing_itemid", "citing_case",
    "document_id",
)
_TARGET_FIELDS = (
    "target_itemid", "target_id", "target", "cited_itemid", "cited_case",
    "citation_id",
)
_SOURCE_APPNO_FIELDS = ("source_appno", "citing_appno", "application_number")
_TARGET_APPNO_FIELDS = ("target_appno", "cited_appno", "citation_appno", "appno")


def _first(row: dict[str, Any], names: Iterable[str]) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".zip":
        with zipfile.ZipFile(path) as archive:
            supported = [
                name for name in archive.namelist()
                if Path(name).suffix.casefold() in {".json", ".jsonl", ".csv", ".tsv"}
            ]
            if len(supported) != 1:
                raise ValueError("benchmark ZIP must contain exactly one supported data file")
            name = supported[0]
            payload = archive.read(name).decode("utf-8")
            suffix = Path(name).suffix.casefold()
            if suffix == ".jsonl":
                return [json.loads(line) for line in payload.splitlines() if line]
            if suffix in {".csv", ".tsv"}:
                return list(csv.DictReader(
                    payload.splitlines(), delimiter="\t" if suffix == ".tsv" else ","
                ))
            value = json.loads(payload)
            return _json_rows(value)
    suffix = path.suffix.casefold()
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        return _json_rows(value)
    if suffix in {".csv", ".tsv"}:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ","))
    if suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(path).to_dict("records")
    raise ValueError(f"unsupported benchmark format: {path.suffix}")


def _json_rows(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("citations", "edges", "records"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            if all(isinstance(row, dict) for row in value.values()):
                return [{"appno": key, **row} for key, row in value.items()]
    if not isinstance(value, list):
        raise ValueError("benchmark JSON must contain a record list or application mapping")
    return [dict(row) for row in value]


def load_benchmark_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read a portable benchmark, occurrence, or study-label table."""
    return _rows(Path(path))


def load_competitor_citations(path: str | Path) -> list[dict[str, Any]]:
    """Normalize an ECtHR-PCR or generic citation export without bundling it.

    Unknown source fields are retained under ``source_record``. Rows without
    any target document or application identity are preserved for audit.
    """
    records: list[dict[str, Any]] = []
    for ordinal, row in enumerate(_rows(Path(path))):
        if isinstance(row.get("citations"), list):
            source_appno = _first(row, ("appno", *_SOURCE_APPNO_FIELDS))
            for citation_ordinal, target in enumerate(row["citations"]):
                records.append({
                    "ordinal": f"{ordinal}:{citation_ordinal}",
                    "source_itemid": _first(row, _SOURCE_FIELDS),
                    "source_appno": source_appno,
                    "target_itemid": None,
                    "target_appno": str(target).strip() or None,
                    "source_component": None,
                    "opinion_id": None,
                    "paragraph": None,
                    "raw_text": None,
                    "source_record": row,
                })
            continue
        records.append({
            "ordinal": ordinal,
            "source_itemid": _first(row, _SOURCE_FIELDS),
            "source_appno": _first(row, _SOURCE_APPNO_FIELDS),
            "target_itemid": _first(row, _TARGET_FIELDS),
            "target_appno": _first(row, _TARGET_APPNO_FIELDS),
            "source_component": _first(row, ("source_component", "component", "section")),
            "opinion_id": _first(row, ("opinion_id", "separate_opinion_id")),
            "paragraph": _first(row, ("paragraph", "paragraph_id", "para_id")),
            "raw_text": _first(row, ("raw_text", "citation", "context", "text")),
            "source_record": row,
        })
    return records


def compare_citation_exports(
    local_rows: Iterable[dict[str, Any]],
    competitor_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compare exact document edges first, then application-level identities."""

    def keys(rows: Iterable[dict[str, Any]], source: str, target: str) -> set[tuple[str, str]]:
        return {
            (str(row[source]), str(row[target]))
            for row in rows
            if row.get(source) and row.get(target)
        }

    local = list(local_rows)
    competitor = list(competitor_rows)
    local_documents = keys(local, "source_itemid", "target_itemid")
    other_documents = keys(competitor, "source_itemid", "target_itemid")
    local_applications = keys(local, "source_appno", "target_appno")
    other_applications = keys(competitor, "source_appno", "target_appno")
    return {
        "document_edges": {
            "local": len(local_documents),
            "competitor": len(other_documents),
            "shared": len(local_documents & other_documents),
            "local_only": sorted(local_documents - other_documents),
            "competitor_only": sorted(other_documents - local_documents),
        },
        "application_edges": {
            "local": len(local_applications),
            "competitor": len(other_applications),
            "shared": len(local_applications & other_applications),
            "local_only": sorted(local_applications - other_applications),
            "competitor_only": sorted(other_applications - local_applications),
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_benchmark(
    kind: str,
    out_dir: str | Path,
    *,
    revision: str | None = None,
    expected_sha256: str | None = None,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """Fetch and verify a pinned benchmark archive without bundling its data."""
    if kind not in BENCHMARK_SOURCES:
        raise ValueError(f"unknown benchmark {kind!r}")
    source = BENCHMARK_SOURCES[kind]
    pinned_revision = str(source["revision"])
    revision = revision or pinned_revision
    expected_sha256 = expected_sha256 or (
        str(source["archive_sha256"]) if revision == pinned_revision else None
    )
    if expected_sha256 is None:
        raise ValueError("a custom benchmark revision requires expected_sha256")
    repository = str(source["repository"])
    archive_url = f"{repository}/archive/{revision}.zip"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"{kind}-{revision}.zip"
    partial = archive.with_suffix(".zip.part")
    request = urllib.request.Request(archive_url, headers={"User-Agent": "echr-py/0.2"})
    downloaded = 0
    try:
        with opener(request, timeout=timeout) as response, partial.open("wb") as handle:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_archive_bytes:
                raise ValueError(
                    f"benchmark archive is {content_length} bytes; limit is {max_archive_bytes}"
                )
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > max_archive_bytes:
                    raise ValueError(
                        f"benchmark archive exceeds the {max_archive_bytes}-byte limit"
                    )
                handle.write(chunk)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    actual_sha256 = _sha256(partial)
    if actual_sha256 != expected_sha256:
        partial.unlink(missing_ok=True)
        raise ValueError(
            f"benchmark archive checksum mismatch: expected {expected_sha256}, "
            f"got {actual_sha256}"
        )
    partial.replace(archive)
    extracted = out / "source"
    extracted.mkdir(exist_ok=True)
    root = extracted.resolve()
    with zipfile.ZipFile(archive) as bundle:
        extracted_bytes = sum(member.file_size for member in bundle.infolist())
        if extracted_bytes > max_extracted_bytes:
            raise ValueError(
                f"benchmark expands to {extracted_bytes} bytes; "
                f"limit is {max_extracted_bytes}"
            )
        for member in bundle.infolist():
            target = (extracted / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe benchmark archive member: {member.filename}")
        bundle.extractall(extracted)
    manifest = {
        "schema_version": "hudoc-citation-benchmark-source/v1",
        "kind": kind,
        "repository": repository,
        "revision": revision,
        "archive_url": archive_url,
        "archive_sha256": actual_sha256,
        "archive_bytes": downloaded,
        "archive_checksum_verified": True,
        "license": source["license"],
        "license_status": source.get("license_status", "declared by repository"),
        "dataset_url": source.get("dataset"),
        "retrieved_at": dt.datetime.now(dt.UTC).isoformat(),
        "source_root": str(extracted),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


_ITEMID_RE = re.compile(r"(?P<itemid>\d{3}-\d+)(?:\.txt)?$")
_NUMERIC_XML_REF_RE = re.compile(r"&#(?:x([0-9a-fA-F]+)|(\d+));")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _mumford_document_metadata(path: Path) -> dict[str, Any]:
    folder = path.parent.name.removesuffix(".txt")
    parts = folder.split("_")
    match = _ITEMID_RE.search(folder)
    return {
        "source_itemid": match.group("itemid") if match else None,
        "sample_code": parts[0] if parts else None,
        "case_outcome": parts[1] if len(parts) > 1 else None,
        "court_level_code": parts[2] if len(parts) > 2 else None,
        "judicial_output": parts[3] if len(parts) > 3 else None,
    }


def _valid_xml_character(codepoint: int) -> bool:
    return (
        codepoint in {0x9, 0xA, 0xD}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _read_mumford_xml(path: Path) -> tuple[ET.Element, int]:
    raw = path.read_text(encoding="utf-8")
    replacements = 0

    def repair_reference(match: re.Match[str]) -> str:
        nonlocal replacements
        codepoint = int(match.group(1), 16) if match.group(1) else int(match.group(2))
        if _valid_xml_character(codepoint):
            return match.group(0)
        replacements += 1
        return "�"

    repaired = _NUMERIC_XML_REF_RE.sub(repair_reference, raw)
    repaired_characters: list[str] = []
    for character in repaired:
        if _valid_xml_character(ord(character)):
            repaired_characters.append(character)
        else:
            repaired_characters.append("�")
            replacements += 1
    return ET.fromstring("".join(repaired_characters)), replacements


def parse_mumford_xmi(path: str | Path, *, curated: bool | None = None) -> dict[str, Any]:
    """Parse one INCEpTION XMI document while preserving exact source offsets."""
    source = Path(path)
    root, xml_replacements = _read_mumford_xml(source)
    sofa = next((element for element in root if _local_name(element.tag) == "Sofa"), None)
    if sofa is None:
        raise ValueError(f"Mumford XMI has no Sofa text: {source}")
    text = sofa.attrib.get("sofaString", "")
    metadata = _mumford_document_metadata(source)
    is_curated = curated if curated is not None else source.name == "CURATION_USER.xmi"
    annotator = "curator" if is_curated else source.stem
    annotations: list[dict[str, Any]] = []
    for element in root:
        if _local_name(element.tag) != "Span" or not element.attrib.get("Citation"):
            continue
        start = int(element.attrib.get("begin", 0))
        end = int(element.attrib.get("end", 0))
        articles = [
            child.text.strip()
            for child in element
            if _local_name(child.tag) == "ArticleorProtocol" and child.text and child.text.strip()
        ]
        citation = element.attrib.get("Citation", "")
        exact_span = text[start:end]
        identity = {
            "source_itemid": metadata["source_itemid"],
            "start": start,
            "end": end,
            "citation": citation,
            "annotator": annotator,
            "curated": is_curated,
        }
        annotations.append({
            "schema_version": "mumford-citation-context/v1",
            "annotation_id": hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()
            ).hexdigest(),
            **metadata,
            "curated": is_curated,
            "annotator": annotator,
            "source_file": str(source),
            "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "xml_illegal_character_replacements": xml_replacements,
            "start": start,
            "end": end,
            "exact_span": exact_span,
            "citation": citation,
            "citation_appnos": list(dict.fromkeys(
                APPNO_REGEX.findall(f"{citation} {exact_span}")
            )),
            "source_label": element.attrib.get("label"),
            "judicial_consideration": element.attrib.get("JudicialConsideration"),
            "flag": element.attrib.get("Flag"),
            "articles_or_protocols": articles,
        })
    return {
        "document": {
            "schema_version": "mumford-document/v1",
            **metadata,
            "curated": is_curated,
            "annotator": annotator,
            "source_file": str(source),
            "source_text": text,
            "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "xml_illegal_character_replacements": xml_replacements,
        },
        "annotations": annotations,
    }


def import_mumford(source: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Normalize curated and raw Mumford annotations into portable JSONL."""
    root = Path(source)
    candidates = sorted(root.rglob("*.xmi"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    document_path = out / "documents.jsonl"
    annotation_path = out / "annotations.jsonl"
    document_count = 0
    annotation_count = 0
    curated_count = 0
    replacement_count = 0
    with (
        document_path.open("w", encoding="utf-8") as document_handle,
        annotation_path.open("w", encoding="utf-8") as annotation_handle,
    ):
        for path in candidates:
            if "Curation" not in path.parts and "Raw_Annotations" not in path.parts:
                continue
            parsed = parse_mumford_xmi(path)
            portable_source = path.relative_to(root).as_posix()
            document = parsed["document"]
            document["source_file"] = portable_source
            document_handle.write(json.dumps(document, ensure_ascii=False) + "\n")
            document_count += 1
            replacement_count += int(document["xml_illegal_character_replacements"])
            for annotation in parsed["annotations"]:
                annotation["source_file"] = portable_source
                annotation_handle.write(json.dumps(annotation, ensure_ascii=False) + "\n")
                annotation_count += 1
                curated_count += int(bool(annotation["curated"]))
    report = {
        "schema_version": "hudoc-citation-benchmark-import/v1",
        "kind": "mumford",
        "source": str(root.resolve()),
        "documents": document_count,
        "annotations": annotation_count,
        "curated_annotations": curated_count,
        "individual_annotations": annotation_count - curated_count,
        "xml_illegal_character_replacements": replacement_count,
        "documents_jsonl": str(document_path),
        "annotations_jsonl": str(annotation_path),
        "interpretation": "external curated reference annotations, not exhaustive ground truth",
    }
    (out / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def import_benchmark(kind: str, source: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Import a supported external reference without treating it as package data."""
    if kind == "mumford":
        return import_mumford(source, out_dir)
    if kind != "ecthr-pcr":
        raise ValueError(f"unknown benchmark {kind!r}")
    rows = load_competitor_citations(source)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records_path = out / "citations.jsonl"
    records_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    report = {
        "schema_version": "hudoc-citation-benchmark-import/v1",
        "kind": kind,
        "source": str(Path(source).resolve()),
        "records": len(rows),
        "records_jsonl": str(records_path),
        "interpretation": "external comparison data, not exhaustive ground truth",
    }
    (out / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _normal(value: object) -> str:
    return normalize_reference_key(str(value or ""))


def _string_values(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.casefold() in {"nan", "none", "null"}:
            return set()
        if stripped[:1] in {"[", "{"}:
            try:
                decoded = json.loads(stripped)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            else:
                if isinstance(decoded, dict):
                    return {str(key) for key, item in decoded.items() if item}
                return _string_values(decoded)
        return {stripped}
    if isinstance(value, float) and math.isnan(value):
        return set()
    if isinstance(value, Iterable):
        return {str(item) for item in value if item}
    return {str(value)}


def _object_list(value: object) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, Iterable) or isinstance(value, (bytes, dict)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _clean_text(value: object) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return None if not text or text.casefold() in {"nan", "none", "null"} else text


def _integer(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None


def _normalized_offsets(value: str) -> tuple[str, list[int], list[int]]:
    """Return a comparison string and reversible offsets into ``value``.

    Mumford's XMI preserves Word non-breaking spaces, page-control characters,
    and punctuation differently from HUDOC's current HTML conversion.  Letters
    and numbers are stable across those renderings, so the comparison view is
    deliberately restricted to Unicode alphanumerics.  Each comparison
    character retains the exact source interval that produced it.
    """

    characters: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for offset, character in enumerate(value):
        expanded = unicodedata.normalize("NFKD", character).casefold()
        for normalized in expanded:
            if not normalized.isalnum():
                continue
            characters.append(normalized)
            starts.append(offset)
            ends.append(offset + 1)
    return "".join(characters), starts, ends


def _all_occurrences(haystack: str, needle: str) -> tuple[int, bool]:
    """Return the first match and whether the normalized anchor is unique."""

    first = haystack.find(needle)
    if first < 0:
        return -1, False
    return first, haystack.find(needle, first + 1) < 0


_MUMFORD_EXCLUDED_SECTIONS = {
    "appendix",
    "facts",
    "operative",
    "procedure",
    "separate_opinion",
}


def _mumford_scope_exclusion(row: dict[str, Any]) -> str | None:
    component = _clean_text(row.get("source_component"))
    if component not in {None, "majority"}:
        return "outside_reference_component"
    if _clean_text(row.get("source_opinion_id")):
        return "outside_reference_opinion"
    if _clean_text(row.get("source_footnote_id")):
        return "outside_reference_footnote"
    section = _clean_text(row.get("source_section"))
    if section in _MUMFORD_EXCLUDED_SECTIONS:
        return "outside_reference_section"
    return None


def _project_mumford_row(
    row: dict[str, Any],
    *,
    sofa_text: str,
    sofa_normalized: str,
    sofa_starts: list[int],
    sofa_ends: list[int],
    source_text_sha256: str,
) -> tuple[dict[str, Any] | None, str]:
    """Project one rich-HUDOC occurrence into Mumford's XMI Sofa offsets.

    The full normalized paragraph is preferred.  If the current HTML and the
    historical XMI paragraph differ elsewhere, progressively smaller windows
    around the actual occurrence are tried.  A window is accepted only when it
    occurs exactly once in the Sofa.  Ambiguity is an abstention.
    """

    excluded = _mumford_scope_exclusion(row)
    if excluded:
        return None, excluded
    context = _clean_text(row.get("source_context") or row.get("context_text"))
    block_start = _integer(row.get("block_start"))
    block_end = _integer(row.get("block_end"))
    if context is None or block_start is None or block_end is None:
        return None, "missing_block_address"
    if not 0 <= block_start < block_end <= len(context):
        return None, "invalid_block_address"

    normalized, starts, ends = _normalized_offsets(context)
    positions = [
        ordinal
        for ordinal, original in enumerate(starts)
        if block_start <= original < block_end
    ]
    if not normalized or not positions:
        return None, "empty_normalized_span"
    span_start = positions[0]
    span_end = positions[-1] + 1

    windows: list[tuple[int, int, str]] = [(0, len(normalized), "normalized_block")]
    for radius in (240, 160, 100, 60, 32):
        left = max(0, span_start - radius)
        right = min(len(normalized), span_end + radius)
        candidate = (left, right, f"normalized_window_{radius}")
        if candidate[:2] not in {(value[0], value[1]) for value in windows}:
            windows.append(candidate)

    saw_ambiguous = False
    for left, right, method in windows:
        anchor = normalized[left:right]
        if len(anchor) < 16:
            continue
        target_anchor, unique = _all_occurrences(sofa_normalized, anchor)
        if target_anchor < 0:
            continue
        if not unique:
            saw_ambiguous = True
            continue
        projected_normal_start = target_anchor + span_start - left
        projected_normal_end = target_anchor + span_end - left
        if not 0 <= projected_normal_start < projected_normal_end <= len(sofa_starts):
            continue
        projected_start = sofa_starts[projected_normal_start]
        projected_end = sofa_ends[projected_normal_end - 1]
        source_canonical_start = starts[span_start]
        source_canonical_end = ends[span_end - 1]
        projected_text = sofa_text[projected_start:projected_end]
        projected_normalized, _, _ = _normalized_offsets(projected_text)
        source_span_normalized = normalized[span_start:span_end]
        if projected_normalized != source_span_normalized:
            continue
        projected = dict(row)
        projected.update({
            "hudoc_document_start": _integer(row.get("document_start")),
            "hudoc_document_end": _integer(row.get("document_end")),
            "document_start": projected_start,
            "document_end": projected_end,
            "benchmark_coordinate_system": "mumford_xmi_sofa",
            "benchmark_source_text_sha256": source_text_sha256,
            "benchmark_projection_method": method,
            "benchmark_projected_text": projected_text,
            "benchmark_projection_reversible": True,
            "benchmark_source_canonical_start": source_canonical_start,
            "benchmark_source_canonical_end": source_canonical_end,
        })
        return projected, method
    return None, "ambiguous_normalized_context" if saw_ambiguous else "unmapped_context"


def project_mumford_occurrences(
    documents: Iterable[dict[str, Any]],
    local_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project full inclusive occurrences onto imported Mumford Sofa offsets.

    Only uniquely mapped majority occurrences inside the supplied XMI text are
    returned.  Facts, operative text, opinions, appendices, and footnotes are
    counted as out of the reference scope rather than treated as negatives.
    """

    curated_documents = {
        str(row.get("source_itemid")): row
        for row in documents
        if row.get("curated", True) and row.get("source_itemid") and row.get("source_text") is not None
    }
    prepared: dict[str, tuple[str, str, list[int], list[int], str]] = {}
    invalid_documents: set[str] = set()
    for itemid, document in curated_documents.items():
        sofa_text = str(document.get("source_text") or "")
        checksum = hashlib.sha256(sofa_text.encode()).hexdigest()
        declared = _clean_text(document.get("source_text_sha256"))
        if declared and declared != checksum:
            invalid_documents.add(itemid)
            continue
        normalized, starts, ends = _normalized_offsets(sofa_text)
        prepared[itemid] = (sofa_text, normalized, starts, ends, checksum)

    projected: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    diagnostics: list[dict[str, Any]] = []
    input_rows = list(local_rows)
    input_source_documents = {
        str(row.get("source_itemid"))
        for row in input_rows
        if row.get("source_itemid")
    }
    rows = [
        row
        for row in input_rows
        if str(row.get("source_itemid") or "") in curated_documents
    ]
    for ordinal, row in enumerate(rows):
        itemid = str(row.get("source_itemid") or "")
        occurrence_id = _clean_text(row.get("occurrence_id")) or f"occurrence:{ordinal}"
        if itemid in invalid_documents:
            reason = "reference_text_hash_mismatch"
            value = None
        elif itemid not in prepared:
            reason = "missing_reference_document"
            value = None
        else:
            sofa_text, normalized, starts, ends, checksum = prepared[itemid]
            value, reason = _project_mumford_row(
                row,
                sofa_text=sofa_text,
                sofa_normalized=normalized,
                sofa_starts=starts,
                sofa_ends=ends,
                source_text_sha256=checksum,
            )
        reasons[reason] += 1
        if value is not None:
            methods[reason] += 1
            projected.append(value)
        else:
            diagnostics.append({
                "occurrence_id": occurrence_id,
                "source_itemid": itemid or None,
                "status": reason,
            })

    report = {
        "schema_version": "mumford-offset-projection/v1",
        "coordinate_system": "mumford_xmi_sofa",
        "reference_documents": len(curated_documents),
        "valid_reference_documents": len(prepared),
        "input_source_documents": len(input_source_documents),
        "local_source_documents": len({
            str(row.get("source_itemid")) for row in rows if row.get("source_itemid")
        }),
        "projected_source_documents": len({
            str(row.get("source_itemid")) for row in projected if row.get("source_itemid")
        }),
        "input_occurrences": len(input_rows),
        "out_of_reference_occurrences": len(input_rows) - len(rows),
        "local_occurrences": len(rows),
        "projected_occurrences": len(projected),
        "abstained_occurrences": len(rows) - len(projected),
        "statuses": dict(sorted(reasons.items())),
        "methods": dict(sorted(methods.items())),
        "matching_rule": (
            "Unicode-alphanumeric normalized block/window must occur exactly once "
            "in the document's XMI Sofa"
        ),
        "diagnostics": diagnostics,
    }
    return projected, report


def _row_id(row: dict[str, Any], ordinal: int, *, prefix: str) -> str:
    value = row.get("annotation_id") if prefix == "annotation" else row.get("occurrence_id")
    return str(value) if value else f"{prefix}:{ordinal}"


def _coordinate_system_matches(
    annotation: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    """Prevent unrelated XMI and full-document offsets from overlapping by chance."""

    annotation_hash = _clean_text(annotation.get("source_text_sha256"))
    if annotation_hash is None:
        return True
    candidate_hash = _clean_text(
        candidate.get("benchmark_source_text_sha256")
        or candidate.get("source_text_sha256")
    )
    return candidate_hash == annotation_hash


def _identity_values(candidate: dict[str, Any]) -> set[str]:
    cached = candidate.get("_benchmark_identity_values")
    if isinstance(cached, set):
        return cached
    values = {
        _normal(
            candidate.get("raw_text")
            or candidate.get("raw_span")
            or candidate.get("raw_citation")
        )
    }
    evidence = candidate.get("evidence")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = None
    if isinstance(evidence, dict):
        values.update({
            _normal(evidence.get("alias")),
            _normal(evidence.get("scl_reference")),
        })
    return {value for value in values if value}


def _identity_compatible(
    annotation: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    """Require bibliographic evidence before accepting even an overlapping span."""
    external_appnos = _string_values(annotation.get("citation_appnos"))
    local_appnos = candidate.get("_benchmark_target_appnos")
    if not isinstance(local_appnos, set):
        local_appnos = _string_values(candidate.get("target_appnos") or candidate.get("appnos"))
    if external_appnos and local_appnos and not external_appnos.intersection(local_appnos):
        return False

    citation = _normal(annotation.get("citation"))
    expected = {citation} if citation else set()
    # The annotated span is coordinate evidence, not an authority identity: a
    # broad span can contain several citations while its feature points to only
    # one of them.  Fall back to it only when the reference supplies no parsed
    # citation or application number at all.
    if not expected and not external_appnos:
        exact_span = _normal(annotation.get("exact_span"))
        if exact_span:
            expected.add(exact_span)
    for reference in expected:
        for observed in _identity_values(candidate):
            if min(len(reference), len(observed)) >= 4 and (
                reference == observed or reference in observed or observed in reference
            ):
                return True
    if external_appnos and external_appnos & local_appnos:
        return True
    reference_target = _clean_text(annotation.get("target_itemid"))
    local_target = _clean_text(candidate.get("target_itemid"))
    return bool(reference_target and reference_target == local_target)


def _alignment_evidence(
    annotation: dict[str, Any], candidate: dict[str, Any]
) -> tuple[int, list[str], str] | None:
    """Return conservative pair evidence and its evaluation family."""
    methods: list[str] = []
    annotation_start = _integer(annotation.get("start"))
    annotation_end = _integer(annotation.get("end"))
    candidate_start = _integer(candidate.get("document_start"))
    candidate_end = _integer(candidate.get("document_end"))
    coordinate_match = _coordinate_system_matches(annotation, candidate)
    if (
        coordinate_match
        and isinstance(annotation_start, int)
        and isinstance(annotation_end, int)
        and annotation_start == candidate_start
        and annotation_end == candidate_end
    ):
        methods.append("source_offsets")
    elif (
        coordinate_match
        and isinstance(annotation_start, int)
        and isinstance(annotation_end, int)
        and isinstance(candidate_start, int)
        and isinstance(candidate_end, int)
        and max(annotation_start, candidate_start) < min(annotation_end, candidate_end)
    ):
        methods.append("source_overlap")

    citation = _normal(annotation.get("citation"))
    exact_span = _normal(annotation.get("exact_span"))
    raw = str(candidate.get("_benchmark_normal_raw") or _normal(
        candidate.get("raw_text")
        or candidate.get("raw_span")
        or candidate.get("raw_citation")
    ))
    context = str(candidate.get("_benchmark_normal_context") or _normal(
        candidate.get("source_context") or candidate.get("context_text")
    ))
    if exact_span and raw == exact_span:
        methods.append("exact_span")
    elif exact_span and raw and (exact_span in raw or raw in exact_span):
        methods.append("span_containment")
    if citation and (citation == raw or citation in context):
        methods.append("normalized_citation")
    external_appnos = _string_values(annotation.get("citation_appnos"))
    local_appnos = candidate.get("_benchmark_target_appnos")
    if not isinstance(local_appnos, set):
        local_appnos = _string_values(candidate.get("target_appnos") or candidate.get("appnos"))
    if external_appnos and external_appnos & local_appnos:
        methods.append("target_appno")
    if not methods or not _identity_compatible(annotation, candidate):
        return None
    weights = {
        "source_offsets": 1000,
        "source_overlap": 750,
        "exact_span": 500,
        "span_containment": 250,
        "normalized_citation": 20,
        "target_appno": 5,
    }
    family = (
        "strict_source_span"
        if "source_offsets" in methods or "source_overlap" in methods
        else "normalized_identity_context"
    )
    return sum(weights[method] for method in methods), methods, family


def align_benchmark_annotations(
    annotations: Iterable[dict[str, Any]], local_rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Align references one-to-one to occurrences using conservative evidence.

    Older callers receive the same row-oriented shape, but a local occurrence
    can no longer be counted as recovery for more than one reference annotation.
    """
    annotation_rows = list(annotations)
    occurrence_rows = []
    for row in local_rows:
        candidate = dict(row)
        candidate["_benchmark_normal_raw"] = _normal(
            candidate.get("raw_text")
            or candidate.get("raw_span")
            or candidate.get("raw_citation")
        )
        candidate["_benchmark_normal_context"] = _normal(
            candidate.get("source_context") or candidate.get("context_text")
        )
        candidate["_benchmark_target_appnos"] = _string_values(
            candidate.get("target_appnos") or candidate.get("appnos")
        )
        candidate["_benchmark_identity_values"] = _identity_values(candidate)
        occurrence_rows.append(candidate)
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in occurrence_rows:
        by_source.setdefault(str(row.get("source_itemid") or ""), []).append(row)
    proposals: list[dict[str, Any]] = []
    for annotation_ordinal, annotation in enumerate(annotation_rows):
        candidates = by_source.get(str(annotation.get("source_itemid") or ""), [])
        ranked: list[tuple[int, dict[str, Any], list[str], str, str]] = []
        for candidate_ordinal, candidate in enumerate(candidates):
            evidence = _alignment_evidence(annotation, candidate)
            if evidence:
                score, methods, family = evidence
                ranked.append((
                    score,
                    candidate,
                    methods,
                    family,
                    _row_id(candidate, candidate_ordinal, prefix="occurrence"),
                ))
        if not ranked:
            proposals.append({
                "ordinal": annotation_ordinal,
                "annotation": annotation,
                "status": "unmatched",
            })
            continue
        best_score = max(value[0] for value in ranked)
        best = [value for value in ranked if value[0] == best_score]
        if len(best) != 1:
            proposals.append({
                "ordinal": annotation_ordinal,
                "annotation": annotation,
                "status": "ambiguous",
                "candidate_occurrence_ids": sorted(
                    value[4] for value in best
                ),
            })
            continue
        score, candidate, methods, family, candidate_id = best[0]
        proposals.append({
            "ordinal": annotation_ordinal,
            "annotation": annotation,
            "status": "proposed",
            "score": score,
            "candidate": candidate,
            "candidate_id": candidate_id,
            "methods": methods,
            "family": family,
        })

    # Claim occurrences in descending evidence order. Equal-score collisions
    # are abstentions: assigning either annotation would be arbitrary.
    collisions: set[int] = set()
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for proposal in proposals:
        if proposal["status"] != "proposed":
            continue
        candidate_id = str(proposal["candidate_id"])
        by_candidate.setdefault(candidate_id, []).append(proposal)
    for values in by_candidate.values():
        best_score = max(int(value["score"]) for value in values)
        leaders = [value for value in values if value["score"] == best_score]
        if len(leaders) > 1:
            collisions.update(int(value["ordinal"]) for value in leaders)

    claimed: set[str] = set()
    for proposal in sorted(
        (value for value in proposals if value["status"] == "proposed"),
        key=lambda value: (-int(value["score"]), int(value["ordinal"])),
    ):
        ordinal = int(proposal["ordinal"])
        candidate_id = str(proposal["candidate_id"])
        if ordinal in collisions or candidate_id in claimed:
            proposal["status"] = "ambiguous"
            proposal["candidate_occurrence_ids"] = [candidate_id]
            continue
        proposal["status"] = "matched"
        claimed.add(candidate_id)

    aligned: list[dict[str, Any]] = []
    for proposal in sorted(proposals, key=lambda value: int(value["ordinal"])):
        annotation = proposal["annotation"]
        if proposal["status"] == "unmatched":
            aligned.append({**annotation, "alignment_status": "unmatched", "occurrence_id": None})
            continue
        if proposal["status"] == "ambiguous":
            aligned.append({
                **annotation,
                "alignment_status": "ambiguous",
                "occurrence_id": None,
                "candidate_occurrence_ids": proposal["candidate_occurrence_ids"],
            })
            continue
        candidate = proposal["candidate"]
        methods = proposal["methods"]
        target_itemid = _clean_text(candidate.get("target_itemid"))
        target_paragraphs = sorted(_string_values(candidate.get("target_paragraphs")))
        paragraph_resolutions = _object_list(candidate.get("target_paragraph_resolutions"))
        paragraph_status = _clean_text(
            candidate.get("paragraph_resolution_status")
            or candidate.get("target_paragraph_status")
        )
        aligned.append({
            **annotation,
            "alignment_status": methods[0],
            "alignment_family": proposal["family"],
            "alignment_methods": methods,
            "occurrence_id": candidate.get("occurrence_id"),
            "local_target_itemid": target_itemid,
            "local_target_appnos": sorted(_string_values(
                candidate.get("target_appnos") or candidate.get("appnos")
            )),
            "local_target_paragraphs": target_paragraphs,
            "local_target_paragraph_resolutions": paragraph_resolutions,
            "local_target_paragraph_status": paragraph_status,
            "local_resolution_scope": _clean_text(candidate.get("resolution_scope")),
            "local_source_component": _clean_text(candidate.get("source_component")),
            "local_scl_coverage": _clean_text(candidate.get("scl_coverage")),
            "benchmark_projection_method": _clean_text(
                candidate.get("benchmark_projection_method")
            ),
        })
    return aligned


def _macro_f1(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    labels = sorted({value for pair in pairs for value in pair})
    values = []
    for label in labels:
        tp = sum(gold == pred == label for gold, pred in pairs)
        fp = sum(gold != label and pred == label for gold, pred in pairs)
        fn = sum(gold == label and pred != label for gold, pred in pairs)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(values) / len(values)


def _nominal_alpha(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    observed = sum(left != right for left, right in pairs) / len(pairs)
    values = [value for pair in pairs for value in pair]
    counts = {value: values.count(value) for value in set(values)}
    total = len(values)
    expected = 1 - sum((count / total) ** 2 for count in counts.values())
    return 1 - observed / expected if expected else 1.0


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _reference_paragraphs(row: dict[str, Any]) -> set[str]:
    for field in (
        "target_paragraphs", "cited_paragraphs", "paragraph_pinpoints", "pinpoints"
    ):
        values = _string_values(row.get(field))
        if values:
            return values
    return set()


def _paragraph_resolution_complete(row: dict[str, Any]) -> bool:
    status = str(row.get("local_target_paragraph_status") or "")
    if status == "resolved":
        return True
    resolutions = row.get("local_target_paragraph_resolutions")
    return isinstance(resolutions, list) and bool(resolutions) and all(
        isinstance(value, dict) and value.get("status") in {"exact", "range"}
        for value in resolutions
    )


def benchmark_citation_annotations(
    annotations: Iterable[dict[str, Any]],
    local_rows: Iterable[dict[str, Any]],
    *,
    labels: Iterable[dict[str, Any]] = (),
    reference_scope: Literal["all", "echr"] = "all",
) -> dict[str, Any]:
    """Evaluate recovery and optional labels against an external reference set."""
    if reference_scope not in {"all", "echr"}:
        raise ValueError("reference_scope must be 'all' or 'echr'")
    all_curated = [row for row in annotations if row.get("curated", True)]
    curated = (
        [
            row for row in all_curated
            if str(row.get("source_label") or "").casefold() == "echr case law"
        ]
        if reference_scope == "echr" else all_curated
    )
    aligned = align_benchmark_annotations(curated, local_rows)
    matched = [row for row in aligned if row.get("occurrence_id")]
    label_by_occurrence = {
        str(row.get("occurrence_id")): row for row in labels if row.get("occurrence_id")
    }
    pairs: list[tuple[str, str]] = []
    for row in matched:
        local = label_by_occurrence.get(str(row["occurrence_id"]))
        gold = str(row.get("judicial_consideration") or "").casefold()
        label_payload = (local or {}).get("data")
        if not isinstance(label_payload, dict):
            label_payload = local or {}
        predicted = str(
            label_payload.get("consideration") or label_payload.get("treatment") or ""
        ).casefold()
        if gold and predicted:
            pairs.append((gold, predicted))
    strict = [row for row in matched if row.get("alignment_family") == "strict_source_span"]
    contextual = [
        row for row in matched
        if row.get("alignment_family") == "normalized_identity_context"
    ]
    exact_document_resolved = sum(bool(row.get("local_target_itemid")) for row in matched)
    document_reference = [row for row in matched if _clean_text(row.get("target_itemid"))]
    exact_document_correct = sum(
        _clean_text(row.get("target_itemid")) == _clean_text(row.get("local_target_itemid"))
        for row in document_reference
    )
    application_reference = [row for row in matched if row.get("citation_appnos")]
    application_identified = [
        row for row in application_reference if row.get("local_target_appnos")
    ]
    application_correct = sum(
        bool(_string_values(row.get("citation_appnos")) & set(row["local_target_appnos"]))
        for row in application_reference
    )
    pinpoint_reference = [row for row in matched if _reference_paragraphs(row)]
    pinpoint_exact = sum(
        _reference_paragraphs(row) == _string_values(row.get("local_target_paragraphs"))
        for row in pinpoint_reference
    )
    local_pinpoints = [row for row in matched if row.get("local_target_paragraphs")]
    target_paragraph_complete = sum(_paragraph_resolution_complete(row) for row in local_pinpoints)
    resolution_abstentions = sum(
        not row.get("local_target_itemid")
        or row.get("local_resolution_scope") in {"application", "unresolved"}
        for row in matched
    )
    report = {
        "schema_version": "hudoc-citation-benchmark-report/v2",
        "reference": "Mumford curated annotations",
        "reference_warning": "not exhaustive ground truth; precision is not inferred from absent annotations",
        "reference_scope": reference_scope,
        "all_curated_annotations": len(all_curated),
        "excluded_reference_annotations": len(all_curated) - len(curated),
        "reference_annotations": len(curated),
        "matching_contract": (
            "one reference annotation to at most one local occurrence; source-span "
            "evidence is accepted only in a verified coordinate system and with "
            "compatible bibliographic identity"
        ),
        "aligned": len(matched),
        "ambiguous": sum(row.get("alignment_status") == "ambiguous" for row in aligned),
        "unmatched": sum(row.get("alignment_status") == "unmatched" for row in aligned),
        "annotated_reference_recovery": len(matched) / len(curated) if curated else None,
        "occurrence_alignment": {
            "denominator": len(curated),
            "strict_source_span": len(strict),
            "strict_source_span_recall": _ratio(len(strict), len(curated)),
            "normalized_identity_context": len(contextual),
            "normalized_identity_context_recall": _ratio(len(contextual), len(curated)),
            "total_one_to_one": len(matched),
            "total_one_to_one_recall": _ratio(len(matched), len(curated)),
            "ambiguous_abstentions": sum(
                row.get("alignment_status") == "ambiguous" for row in aligned
            ),
            "unmatched": sum(row.get("alignment_status") == "unmatched" for row in aligned),
        },
        # Compatibility count retained for v1 report readers.
        "exact_document_resolved": exact_document_resolved,
        "exact_document_resolution": {
            "aligned_denominator": len(matched),
            "resolved": exact_document_resolved,
            "resolved_rate": _ratio(exact_document_resolved, len(matched)),
            "reference_identity_denominator": len(document_reference),
            "verified_correct": exact_document_correct,
            "verified_accuracy": _ratio(exact_document_correct, len(document_reference)),
        },
        "application_identification": {
            "reference_identity_denominator": len(application_reference),
            "automatically_identified": len(application_identified),
            "matching_printed_appno": application_correct,
            "conflicting_printed_appno": len(application_identified) - application_correct,
            "not_identified": len(application_reference) - len(application_identified),
            "identification_coverage": _ratio(
                application_correct, len(application_reference)
            ),
            "agreement_given_identification": _ratio(
                application_correct, len(application_identified)
            ),
            "metric_note": (
                "Matching printed application numbers measure identification coverage "
                "and conditional agreement, not exact procedural-document accuracy."
            ),
            # Compatibility fields retained for v2 report readers.  The rate
            # is the same as identification_coverage, not a precision claim.
            "verified_correct": application_correct,
            "verified_accuracy": _ratio(application_correct, len(application_reference)),
        },
        "pinpoint_recovery": {
            "reference_pinpoint_denominator": len(pinpoint_reference),
            "exact_label_matches": pinpoint_exact,
            "exact_label_recall": _ratio(pinpoint_exact, len(pinpoint_reference)),
            "coverage_only_when_reference_denominator_is_zero": not pinpoint_reference,
        },
        "target_paragraph_resolution": {
            "local_pinpoint_denominator": len(local_pinpoints),
            "completely_mapped": target_paragraph_complete,
            "complete_mapping_rate": _ratio(target_paragraph_complete, len(local_pinpoints)),
            "accuracy_not_inferred_without_reference_targets": True,
        },
        "paragraph_pinpoint_resolved": sum(bool(row.get("local_target_paragraphs")) for row in matched),
        "resolution_abstentions": resolution_abstentions,
        "source_components": {
            component: sum(row.get("local_source_component") == component for row in matched)
            for component in sorted({
                str(row["local_source_component"])
                for row in matched
                if row.get("local_source_component") is not None
            })
        },
        "labelled_overlap": len(pairs),
        "label_abstentions": len(matched) - len(pairs),
        "treatment_macro_f1": _macro_f1(pairs),
        "treatment_krippendorff_alpha": _nominal_alpha(pairs),
        "published_context_proxy": {
            "metric": "outcome-classification F1 on the paper's 27-case intersection",
            "human": 0.746,
            "ai": 0.711,
            "comparable_to_direct_label_agreement": False,
        },
        "alignments": aligned,
    }
    return report
