"""Discover and acquire every HUDOC language version of a document."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .models import AcquisitionManifest, DocumentVersion, FormatOutcome, ManifestFile

_ATTRIBUTION_RE = re.compile(
    r"(?:\b(?:legal\s+)?summary\s+by\s+|\btranslation\]\s+by\s+)(.+?)\s*$",
    re.IGNORECASE,
)


def classify_case_version(case) -> DocumentVersion:
    """Classify a HUDOC row without pretending every appno match is a judgment."""
    language = (case.language or "").upper()
    doctype = (case.doctype or "").upper()
    title = case.docname or ""
    lower_title = title.casefold()
    is_summary = doctype in {"CLIN", "CLINF"} or "summary" in lower_title
    is_translation = language not in {"ENG", "FRE"} or "translation" in lower_title
    attribution_match = _ATTRIBUTION_RE.search(title)
    document_kind: Literal[
        "judgment", "decision", "advisory_opinion", "resolution", "legal_summary", "other"
    ]
    if is_summary and doctype in {"CLIN", "CLINF"}:
        document_kind = "legal_summary"
    elif "JUD" in doctype:
        document_kind = "judgment"
    elif "DEC" in doctype:
        document_kind = "decision"
    elif "ADO" in doctype:
        document_kind = "advisory_opinion"
    elif "RES" in doctype:
        document_kind = "resolution"
    else:
        document_kind = "other"

    rendition_kind: Literal[
        "official_text", "official_summary", "translation", "translated_summary", "other"
    ]
    if is_summary and is_translation:
        rendition_kind = "translated_summary"
    elif is_summary:
        rendition_kind = "official_summary"
    elif is_translation:
        rendition_kind = "translation"
    elif language in {"ENG", "FRE"}:
        rendition_kind = "official_text"
    else:
        rendition_kind = "other"
    return DocumentVersion(
        itemid=case.itemid,
        language=language,
        appno=case.appno,
        ecli=case.ecli,
        docname=case.docname,
        doctype=case.doctype,
        typedescription=case.typedescription,
        document_collection=case.document_collection_id,
        document_kind=document_kind,
        rendition_kind=rendition_kind,
        is_official_language=language in {"ENG", "FRE"},
        is_official_text=rendition_kind in {"official_text", "official_summary"},
        is_translation=is_translation,
        is_summary=is_summary,
        translation_attribution=(attribution_match.group(1) if attribution_match else None),
        published_by=case.published_by,
        external_sources=case.external_sources,
    )


async def list_versions(
    *, appno: str | None = None, ecli: str | None = None, limit: int = 500
) -> list[DocumentVersion]:
    """List distinct HUDOC records across all languages.

    Multiple records in the same language are retained: an itemid, not a
    language code, is the downloadable-version identity.
    """
    if not (appno or ecli):
        raise ValueError("Provide appno or ecli")
    from . import aio

    cases = await aio.search(
        appno=appno,
        ecli=ecli,
        languages=(),
        doctypes=(),
        limit=limit,
        sort="date-desc",
    )
    return [classify_case_version(case) for case in cases]


async def download_versions(
    output_dir: str | Path,
    *,
    appno: str | None = None,
    ecli: str | None = None,
    languages: Iterable[str] | None = None,
    formats: Iterable[str] = ("html", "txt", "md", "docx"),
    concurrency: int = 10,
) -> AcquisitionManifest:
    """Discover and download selected formats, then write ``manifest.json``."""
    from . import __version__
    from .main.downloader import AsyncDocumentDownloader

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    discovered_versions = await list_versions(appno=appno, ecli=ecli)
    versions = discovered_versions
    wanted_languages = {value.upper() for value in languages or ()}
    if wanted_languages:
        versions = [version for version in versions if version.language in wanted_languages]
    requested = {value.lower() for value in formats}
    unknown = requested - {"html", "txt", "md", "docx"}
    if unknown:
        raise ValueError(f"Unknown formats: {sorted(unknown)}")

    downloader = AsyncDocumentDownloader(
        root,
        save_html="html" in requested,
        save_txt="txt" in requested,
        save_md="md" in requested,
        save_docx="docx" in requested,
    )
    results = await downloader.download_batch(
        [version.itemid for version in versions], concurrency=concurrency
    )
    manifest = AcquisitionManifest(
        package_version=__version__,
        created_at=started_at,
        query={k: v for k, v in {"appno": appno, "ecli": ecli}.items() if v},
        selection_mode="exact_document" if ecli else "application_records",
        requested_formats=sorted(requested),
        discovered_count=len(discovered_versions),
        selected_count=len(versions),
        versions=versions,
    )
    for version in versions:
        files: list[ManifestFile] = []
        outcomes: list[FormatOutcome] = []
        for fmt in sorted(requested):
            path = root / fmt / f"{version.itemid}.{fmt}"
            file_record: ManifestFile | None = None
            if path.is_file():
                data = path.read_bytes()
                file_record = ManifestFile(
                    path=path.relative_to(root).as_posix(),
                    format=fmt,
                    bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
                files.append(file_record)
            response = downloader.last_outcomes.get(version.itemid, {}).get(fmt)
            outcomes.append(
                FormatOutcome(
                    format=fmt,
                    status=(
                        response.status
                        if response is not None
                        else "missing"
                        if not results.get(version.itemid, False)
                        else "error"
                    ),
                    url=response.url if response else None,
                    http_status=response.http_status if response else None,
                    content_type=response.content_type if response else None,
                    attempts=response.attempts if response else 0,
                    retrieved_at=response.retrieved_at if response else None,
                    error=response.error if response else "missing_download_outcome",
                    file=file_record,
                )
            )
        manifest.files[version.itemid] = files
        manifest.outcomes[version.itemid] = outcomes
        if any(outcome.status in {"missing", "error"} for outcome in outcomes):
            manifest.failures.append(version.itemid)
    manifest.failures = sorted(set(manifest.failures))
    manifest.completed_at = datetime.now(UTC)
    (root / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
