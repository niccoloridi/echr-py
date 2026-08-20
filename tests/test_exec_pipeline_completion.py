"""Offline coverage for the added HUDOC-EXEC pipeline capabilities."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from hudoc_py.cli import main as cli_main
from hudoc_py.execution import raw_download, textract
from hudoc_py.execution.imports import parse_exec_export
from hudoc_py.execution.related_scraper import (
    RelatedTabScraper,
    ScrapedRelatedDocument,
    _reference_from_url,
    infer_document_type,
)
from hudoc_py.execution.textract import extract_text, looks_scanned


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def read(self):
        return self.body


class FakeSession:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body
        self.urls: list[str] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakeResponse(self.status, self.body)


class FakeClientSession(FakeSession):
    def __init__(self, *args, **kwargs):
        super().__init__(200, b"")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def test_raw_pdf_fetch_uses_content_store_id_and_handles_statuses():
    body = b"%PDF-1.7" + b"x" * 30
    session = FakeSession(200, body)
    assert asyncio.run(raw_download.fetch_exec_document_pdf(session, "store-id")) == body
    assert "library=EXEC&id=store-id" in session.urls[0]
    assert asyncio.run(raw_download.fetch_exec_document_pdf(FakeSession(204, b""), "x")) is None
    assert asyncio.run(raw_download.fetch_exec_document_pdf(FakeSession(200, b"tiny"), "x")) is None


def test_raw_downloader_resume_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({"execidentifier": "old", "status": "ok"}) + "\n",
        encoding="utf-8",
    )

    async def fake_fetch(session, store_id, *, max_retries=3):
        return b"%PDF" + b"x" * 30

    monkeypatch.setattr(raw_download.aiohttp, "ClientSession", FakeClientSession)
    monkeypatch.setattr(raw_download, "fetch_exec_document_pdf", fake_fetch)
    downloader = raw_download.AsyncExecRawDownloader(tmp_path, manifest_path=manifest)
    stats = asyncio.run(
        downloader.download(
            [
                {"execidentifier": "old", "execcontentstoreid": "s-old"},
                {"execidentifier": "new", "execcontentstoreid": "s-new"},
            ]
        )
    )
    assert stats == {"total": 2, "downloaded": 1, "skipped": 1, "failed": 0}
    assert (tmp_path / "pdf" / "new.pdf").exists()


def test_textract_scanned_detection_and_opt_in_ocr(monkeypatch):
    monkeypatch.setattr("hudoc_py.execution.textract.pdf_to_text", lambda data: "")
    monkeypatch.setattr("hudoc_py.execution.textract.ocr_pdf", lambda data, client=None: "OCR text")
    assert looks_scanned("", byte_length=10_000)
    assert not looks_scanned("A" * 200, byte_length=10_000)
    assert extract_text(b"x" * 6_000, file_type="pdf", ocr=False) == ""
    assert extract_text(b"x" * 6_000, file_type="pdf", ocr=True) == "OCR text"


def test_large_short_text_pdf_does_not_trigger_ocr():
    assert not looks_scanned(
        "Short but valid court notice with a reference number.",
        byte_length=2_000_000,
    )
    assert looks_scanned("page 1", page_count=10)


def test_optional_dependency_error_is_actionable():
    try:
        textract._optional("hudoc_py_missing_optional_package", "exec-docs")
    except ImportError as exc:
        assert "install echr-py[exec-docs]" in str(exc)
    else:
        raise AssertionError("missing optional module unexpectedly imported")


def test_mistral_v2_client_import_layout(monkeypatch):
    class FakeMistral:
        def __init__(self, *, api_key):
            self.api_key = api_key

    def fake_optional(module, extra):
        if module == "mistralai":
            return type("Root", (), {})
        if module == "mistralai.client":
            return type("ClientModule", (), {"Mistral": FakeMistral})
        raise AssertionError(module)

    monkeypatch.setattr(textract, "_optional", fake_optional)
    assert textract._mistral_client("secret").api_key == "secret"


def test_html_conversion_needs_no_heavy_dependency():
    text = extract_text(b"<h1>Heading</h1><p>Body</p>", file_type="html")
    assert "Heading" in text and "Body" in text


def test_official_csv_import(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(" execidentifier ,title\nD1,Case\n", encoding="utf-8")
    assert list(parse_exec_export(csv_path).columns) == ["execidentifier", "title"]


def test_related_document_type_inference():
    assert infer_document_type("Government action plan") == "acp"
    assert infer_document_type("Plan d'action du Gouvernement") == "acp"
    assert infer_document_type("Bilan d'action") == "acr"
    assert infer_document_type("NGO communication") == "ngo"
    assert infer_document_type("Communication d'une INDH") == "nhri"
    assert infer_document_type("CM Committee decision") == "CMDEC"
    assert infer_document_type("Unclassified document") == "unknown"
    assert (
        _reference_from_url('/eng#{"display":[2],"execidentifier":["DH-DD(2025)904F"]}')
        == "DH-DD(2025)904F"
    )


def test_related_batch_resume_and_error_checkpoint(tmp_path, monkeypatch):
    checkpoint = tmp_path / "related.jsonl"
    checkpoint.write_text(
        json.dumps({"case_id": "done", "status": "ok", "documents": []}) + "\n",
        encoding="utf-8",
    )
    scraper = RelatedTabScraper()
    calls: list[str] = []

    async def fake_scrape(case_id):
        calls.append(case_id)
        if case_id == "broken":
            raise RuntimeError("page failed")
        return [ScrapedRelatedDocument(case_id=case_id, title="Action plan")]

    monkeypatch.setattr(scraper, "scrape_related", fake_scrape)
    result = asyncio.run(
        scraper.scrape_batch(["done", "new", "broken"], checkpoint_path=checkpoint)
    )
    assert calls == ["new", "broken"]
    assert result["new"][0].document_type == "unknown"
    assert result["broken"] == []
    assert '"status": "error"' in checkpoint.read_text(encoding="utf-8")


def test_cli_raw_text_and_related_document_acquisition(tmp_path, monkeypatch):
    documents_out = tmp_path / "documents-found.jsonl"

    def fake_search_documents(**kwargs):
        assert kwargs == {
            "collection": "acp",
            "state": "ITA",
            "appno": None,
            "master_group_id": None,
            "language": "ENG",
            "limit": 25,
            "page_size": None,
        }
        return [SimpleNamespace(model_dump=lambda **_: {"execidentifier": "D1"})]

    monkeypatch.setattr("hudoc_py.execution.search_documents", fake_search_documents)
    assert (
        cli_main(
            [
                "exec",
                "search-documents",
                "--collection",
                "acp",
                "--state",
                "ITA",
                "--limit",
                "25",
                "--out",
                str(documents_out),
            ]
        )
        == 0
    )
    assert json.loads(documents_out.read_text(encoding="utf-8"))["execidentifier"] == "D1"

    documents = tmp_path / "documents.jsonl"
    documents.write_text('{"execidentifier":"D1"}\n', encoding="utf-8")

    async def fake_download(self, records, **kwargs):
        assert records[0]["execidentifier"] == "D1"
        return {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0}

    monkeypatch.setattr(raw_download.AsyncExecRawDownloader, "download", fake_download)
    assert (
        cli_main(["exec", "download-raw", "--in", str(documents), "--out", str(tmp_path / "raw")])
        == 0
    )

    source = tmp_path / "source.pdf"
    target = tmp_path / "source.txt"
    source.write_bytes(b"%PDF-placeholder")
    monkeypatch.setattr(textract, "extract_text", lambda *args, **kwargs: "converted")
    assert cli_main(["exec", "extract-text", "--in", str(source), "--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == "converted"

    class FakeScraper:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def scrape_batch(self, case_ids, **kwargs):
            assert case_ids == ["004-1"]
            return {"004-1": []}

    monkeypatch.setattr("hudoc_py.execution.related_scraper.RelatedTabScraper", FakeScraper)
    assert (
        cli_main(
            [
                "exec",
                "scrape-related",
                "004-1",
                "--out",
                str(tmp_path / "related.jsonl"),
            ]
        )
        == 0
    )
