from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from hudoc_py.local import build_paragraph_index, get_paragraphs, search_paragraphs
from hudoc_py.models import Case
from hudoc_py.models.version import DocumentVersion
from hudoc_py.text import extract_dispositive_paragraphs, segment_full
from hudoc_py.versions import classify_case_version

FIXTURES = Path(__file__).parent / "fixtures"

JUDGMENT = """PROCEDURE

1. The application was lodged on 1 January.

THE FACTS

2. The applicant lived in Rome.

THE LAW

3. The Court finds a violation of Article 8.

FOR THESE REASONS, THE COURT

1. Declares, unanimously, the application admissible;

2. Holds, by six votes to one, that there has been a violation of Article 8.
"""


def test_dispositive_paragraphs_are_stably_addressed():
    sections = segment_full(JUDGMENT, document_id="001-test")
    rulings = extract_dispositive_paragraphs(sections)
    assert [value.order for value in rulings] == [1, 2]
    assert rulings[0].disposition_id.startswith("001-test:operative:")
    assert rulings[0].vote == "unanimously"
    assert "six votes to one" in (rulings[1].vote or "").lower()


def test_dispositive_continuations_and_formal_end_are_handled():
    text = (FIXTURES / "dispositive_en_continuation.txt").read_text(encoding="utf-8")
    rulings = extract_dispositive_paragraphs(segment_full(text, document_id="en"))
    assert len(rulings) == 3
    assert rulings[1].text.count("\n") == 2
    assert "(a) that the respondent State" in rulings[1].text
    assert len(rulings[1].block_ids) == 3
    assert all("Done in English" not in value.text for value in rulings)


def test_french_dispositive_votes():
    text = (FIXTURES / "dispositive_fr.txt").read_text(encoding="utf-8")
    rulings = extract_dispositive_paragraphs(segment_full(text, document_id="fr"))
    assert len(rulings) == 3
    assert rulings[0].vote == "à l’unanimité"
    assert rulings[1].vote == "par quinze voix contre deux"
    assert all("Fait en français" not in value.text for value in rulings)


def test_paragraph_index_retrieval_and_fts(tmp_path):
    (tmp_path / "texts.jsonl").write_text(
        json.dumps(
            {
                "itemid": "001-test",
                "source_language": "ITA",
                "text": JUDGMENT,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "paragraphs.sqlite"
    stats = build_paragraph_index(tmp_path, database)
    assert stats["documents"] == 1 and stats["paragraphs"] >= 5
    matches = search_paragraphs(database, '"violation of Article 8"')
    assert matches and matches[0]["itemid"] == "001-test"
    paragraphs = get_paragraphs(database, "001-test", para_id="3")
    assert paragraphs[0]["para_num"] == 3


def test_paragraph_index_prefers_rich_opinion_and_footnote_provenance(tmp_path):
    import pandas as pd

    pd.DataFrame(
        [
            {
                "itemid": "001-rich",
                "language": "ENG",
                "block_id": "b1",
                "type": "footnote",
                "text": "[1] Authority cited at paragraph 20.",
                "para_id": "u-1",
                "para_num": None,
                "section": "separate_opinion",
                "footnote_id": "ftn1",
                "opinion_id": "001-rich:opinion:1",
                "opinion_ordinal": 1,
                "opinion_type": "dissenting",
                "opinion_authors": '["SMITH"]',
                "opinion_joined_by": '["JONES"]',
            }
        ]
    ).to_parquet(tmp_path / "paragraphs.parquet", index=False)
    (tmp_path / "language-texts.jsonl").write_text(
        json.dumps(
            {
                "itemid": "001-rich",
                "source_language": "ENG",
                "kp_date": "2024-01-01",
                "text": "source",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    database = tmp_path / "rich.sqlite"
    assert build_paragraph_index(tmp_path, database) == {"documents": 1, "paragraphs": 1}
    row = get_paragraphs(database, "001-rich")[0]

    assert row["source_component"] == "opinion"
    assert row["opinion_type"] == "dissenting"
    assert json.loads(row["opinion_authors"]) == ["SMITH"]
    assert row["footnote_id"] == "ftn1"


def test_document_version_classification_distinguishes_summaries():
    official = classify_case_version(
        Case(itemid="eng", languageisocode="ENG", doctype="HEJUD", docname="CASE OF X")
    )
    assert official.document_kind == "judgment"
    assert official.rendition_kind == "official_text"
    assert official.is_official_text

    translated_summary = classify_case_version(
        Case(
            itemid="rum",
            languageisocode="RUM",
            doctype="HJUDRUM",
            docname="CASE OF X - [Romanian translation] legal summary by IER",
            publishedby="IER",
        )
    )
    assert translated_summary.document_kind == "judgment"
    assert translated_summary.rendition_kind == "translated_summary"
    assert translated_summary.is_translation and translated_summary.is_summary
    assert not translated_summary.is_official_text
    assert translated_summary.translation_attribution == "IER"

    official_summary = classify_case_version(
        Case(itemid="clin", languageisocode="ENG", doctype="CLIN", docname="X v. Y")
    )
    assert official_summary.document_kind == "legal_summary"
    assert official_summary.rendition_kind == "official_summary"


def test_manifest_records_every_requested_format_outcome(tmp_path, monkeypatch):
    from hudoc_py import versions as versions_mod
    from hudoc_py.main import downloader as downloader_mod

    async def fake_versions(**kwargs):
        return [
            DocumentVersion(
                itemid="001-test",
                language="RUM",
                document_kind="judgment",
                rendition_kind="translation",
                is_translation=True,
            )
        ]

    class FakeDownloader:
        def __init__(self, output_root, **kwargs):
            self.root = Path(output_root)
            self.last_outcomes = {}

        async def download_batch(self, itemids, *, concurrency):
            (self.root / "html").mkdir(parents=True)
            (self.root / "txt").mkdir(parents=True)
            (self.root / "html" / "001-test.html").write_text("<p>text</p>")
            (self.root / "txt" / "001-test.txt").write_text("text")
            now = datetime.now(UTC)
            self.last_outcomes = {
                "001-test": {
                    "html": downloader_mod.DownloadResponse(
                        status="downloaded",
                        url="https://example.test/html",
                        http_status=200,
                        content_type="text/html",
                        attempts=1,
                        retrieved_at=now,
                    ),
                    "txt": downloader_mod.DownloadResponse(
                        status="derived",
                        url="https://example.test/html",
                        http_status=200,
                        content_type="text/plain",
                        attempts=1,
                        retrieved_at=now,
                    ),
                    "docx": downloader_mod.DownloadResponse(
                        status="error",
                        url="https://example.test/docx",
                        http_status=404,
                        attempts=1,
                        retrieved_at=now,
                        error="http_status_404",
                    ),
                }
            }
            return {"001-test": True}

    monkeypatch.setattr(versions_mod, "list_versions", fake_versions)
    monkeypatch.setattr(downloader_mod, "AsyncDocumentDownloader", FakeDownloader)
    manifest = asyncio.run(
        versions_mod.download_versions(
            tmp_path, ecli="ECLI:test", formats=["html", "txt", "docx"]
        )
    )
    assert manifest.schema_version == "hudoc-acquisition.v2"
    assert manifest.selection_mode == "exact_document"
    assert manifest.requested_formats == ["docx", "html", "txt"]
    assert manifest.discovered_count == 1 and manifest.selected_count == 1
    assert {value.status for value in manifest.outcomes["001-test"]} == {
        "downloaded",
        "derived",
        "error",
    }
    assert manifest.failures == ["001-test"]
    assert manifest.outcomes["001-test"][1].file is not None
