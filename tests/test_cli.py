"""Smoke tests for the CLI parser. We don't execute network commands here."""

from __future__ import annotations

import json

import pytest

from hudoc_py.cli import build_parser, main


def test_parser_builds_without_error():
    parser = build_parser()
    assert parser is not None


def test_version_flag(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "echr-py" in out


def test_no_command_prints_help(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert "Pythonic access" in out
    assert rc == 0


def test_record_writer_supports_csv(tmp_path):
    from hudoc_py.cli import _write_records

    output = tmp_path / "cases.csv"
    _write_records([{"itemid": "001-1", "language": "ENG"}], str(output))
    assert output.read_text().splitlines() == ["itemid,language", "001-1,ENG"]


def test_search_parser_accepts_filters():
    parser = build_parser()
    args = parser.parse_args(
        [
            "search",
            "--article",
            "3",
            "--respondent",
            "ITA",
            "--limit",
            "50",
            "--page-size",
            "250",
        ]
    )
    assert args.command == "search"
    assert args.article == "3"
    assert args.respondent == "ITA"
    assert args.limit == 50
    assert args.page_size == 250


def test_search_all_requests_complete_result_set():
    parser = build_parser()
    args = parser.parse_args(["search", "--article", "3", "--all"])
    assert args.all is True


def test_search_language_flags_are_repeatable_and_normalised():
    from hudoc_py.cli import _filters_from_args

    parser = build_parser()
    args = parser.parse_args(["search", "--language", "eng", "--language", "FRE"])
    assert _filters_from_args(args)["languages"] == ["ENG", "FRE"]


def test_search_accepts_doctypes_collections_and_hudoc_url():
    from hudoc_py.cli import _filters_from_args

    parser = build_parser()
    args = parser.parse_args(
        [
            "search",
            "--doctype",
            "hecom",
            "--doctype",
            "HFCOM",
            "--collection",
            "COMMUNICATEDCASES",
            "--hudoc-url",
            "https://hudoc.echr.coe.int/eng?i=001-1",
        ]
    )
    filters = _filters_from_args(args)
    assert filters["doctypes"] == ["HECOM", "HFCOM"]
    assert filters["collection"] == ["COMMUNICATEDCASES"]
    assert filters["hudoc_url"].endswith("?i=001-1")


def test_search_rejects_page_size_above_hudoc_limit():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["search", "--page-size", "501"])


def test_fetch_case_requires_one_of_appno_or_itemid():
    rc = main(["fetch-case"])
    assert rc == 2


def test_search_parser_accepts_new_filters_and_sort():
    parser = build_parser()
    args = parser.parse_args(
        [
            "search",
            "--conclusion",
            "Violation of Article 3",
            "--thesaurus",
            "350",
            "--docname",
            "McCann",
            "--body",
            "grand-chamber",
            "--separate-opinion",
            "true",
            "--sort",
            "date-desc",
        ]
    )
    assert args.conclusion == "Violation of Article 3"
    assert args.body == "grand-chamber"
    assert args.separate_opinion == "true"
    assert args.sort == "date-desc"


def test_count_parser():
    parser = build_parser()
    args = parser.parse_args(["count", "--article", "3", "--respondent", "ITA"])
    assert args.command == "count"
    assert args.article == "3"


def test_smart_fetch_parser():
    parser = build_parser()
    args = parser.parse_args(
        [
            "smart-fetch",
            "--text",
            '"positive obligations"',
            "--top",
            "5",
            "--page-size",
            "250",
            "--concurrency",
            "8",
            "--section",
            "the_law",
            "--no-text",
            "--docx-dir",
            "/tmp/docs",
        ]
    )
    assert args.command == "smart-fetch"
    assert args.concurrency == 8
    assert args.page_size == 250
    assert args.top == 5
    assert args.section == "the_law"
    assert args.no_text is True
    assert args.docx_dir == "/tmp/docs"


def test_fetch_case_docx_flag_parses():
    parser = build_parser()
    args = parser.parse_args(["fetch-case", "--appno", "46221/99", "--docx", "/tmp/x.docx"])
    assert args.command == "fetch-case"
    assert args.docx == "/tmp/x.docx"


def test_fetch_case_language_flag_parses():
    parser = build_parser()
    args = parser.parse_args(["fetch-case", "--appno", "46221/99", "--language", "fre"])
    assert args.language == "FRE"


def test_rich_section_flags_parse():
    parser = build_parser()
    fetch = parser.parse_args(["fetch-case", "--itemid", "001-1", "--with-text", "--rich-sections"])
    smart = parser.parse_args(["smart-fetch", "--rich-sections"])
    assert fetch.rich_sections is True
    assert smart.rich_sections is True


def test_citations_locate_parser_and_offline_cache(tmp_path, monkeypatch):
    from hudoc_py.citations import extract_citation_occurrences, parse_scl_mentions
    from hudoc_py.citations.models import CitationCandidate, CitationResolution
    from hudoc_py.models import Case

    case = Case(
        itemid="001-source",
        languageisocode="ENG",
        scl="Soering v. the United Kingdom, 7 July 1989, § 88",
    )
    source = tmp_path / "cases.jsonl"
    source.write_text(case.model_dump_json() + "\n", encoding="utf-8")
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    html = "<p>1. See Soering v. the United Kingdom, 7 July 1989, § 88.</p>"
    (html_dir / "001-source.html").write_text(html, encoding="utf-8")
    mention = parse_scl_mentions(case)[0]
    resolution = CitationResolution(
        mention=mention,
        status="resolved_metadata",
        method="fixture",
        target=CitationCandidate(node_id="ecli:soering", itemid="001-target"),
    )
    monkeypatch.setattr("hudoc_py.citations.load_resolutions", lambda _: [resolution])
    out = tmp_path / "out"
    target_html_dir = tmp_path / "target-html"
    target_html_dir.mkdir()
    (target_html_dir / "001-target.html").write_text(
        "<p>88. The target paragraph.</p>", encoding="utf-8"
    )

    rc = main(
        [
            "citations",
            "locate",
            "--in",
            str(source),
            "--resolution-dir",
            str(tmp_path / "resolved"),
            "--out",
            str(out),
            "--html-dir",
            str(html_dir),
            "--target-html-dir",
            str(target_html_dir),
            "--offline",
        ]
    )

    assert rc == 0
    assert (out / "occurrences.parquet").exists()
    rows = [json.loads(line) for line in (out / "occurrences.jsonl").read_text().splitlines()]
    direct = extract_citation_occurrences(case, [resolution], html=html)
    assert rows[0]["occurrence_id"] == direct.occurrences[0].occurrence_id
    assert rows[0]["source_para_id"] == "1"
    assert rows[0]["target_node_id"] == "ecli:soering"
    assert rows[0]["paragraph_resolution_status"] == "resolved"
    paragraph_edges = [
        json.loads(line)
        for line in (out / "paragraph-edges-inclusive.jsonl").read_text().splitlines()
    ]
    assert paragraph_edges[0]["target_para_num"] == 88
    report = json.loads((out / "occurrence-report.json").read_text())
    assert report["missing_html"] == 0
    assert report["occurrences"] == 1
    manifest = json.loads((out / "source-html-manifest.jsonl").read_text())
    assert manifest["source"] == "cache"
    assert manifest["source_url"].endswith("001-source")
    assert len(manifest["sha256"]) == 64
    catalog_manifest = json.loads((out / "historical-catalog-manifest.json").read_text())
    assert catalog_manifest["entry_count"] > 1000
    assert len(catalog_manifest["content_sha256"]) == 64


def test_citations_locate_offline_missing_html_is_partial_and_nonzero(tmp_path, monkeypatch):
    source = tmp_path / "cases.jsonl"
    source.write_text(json.dumps({"itemid": "001-missing", "scl": "A v. B"}) + "\n")
    monkeypatch.setattr("hudoc_py.citations.load_resolutions", lambda _: [])
    out = tmp_path / "out"

    rc = main(
        [
            "citations",
            "locate",
            "--in",
            str(source),
            "--resolution-dir",
            str(tmp_path / "resolved"),
            "--out",
            str(out),
            "--offline",
        ]
    )

    assert rc == 1
    report = json.loads((out / "occurrence-report.json").read_text())
    assert report["missing_html"] == 1
    assert report["scl_mentions"] == 1
    assert report["unlocated_mentions"] == 1
    assert (out / "occurrences.jsonl").read_text() == ""


def test_citations_locate_fetches_and_checksums_public_html(tmp_path, monkeypatch):
    from hudoc_py.models import Case

    case = Case(
        itemid="001-fetched",
        scl="Soering v. the United Kingdom, 7 July 1989, § 88",
    )
    source = tmp_path / "cases.jsonl"
    source.write_text(case.model_dump_json() + "\n", encoding="utf-8")
    html = "<p>1. See Soering v. the United Kingdom, 7 July 1989, § 88.</p>"

    async def fake_fetch(_session, itemid):
        assert itemid == "001-fetched"
        return html

    monkeypatch.setattr("hudoc_py.main.downloader.fetch_document_html", fake_fetch)
    monkeypatch.setattr("hudoc_py.citations.load_resolutions", lambda _: [])
    out = tmp_path / "out"

    rc = main(
        [
            "citations",
            "locate",
            "--in",
            str(source),
            "--resolution-dir",
            str(tmp_path / "resolved"),
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    assert (out / "source-html" / "001-fetched.html").read_text() == html
    manifest = json.loads((out / "source-html-manifest.jsonl").read_text())
    assert manifest["source"] == "hudoc"
    assert len(manifest["sha256"]) == 64


def test_segment_local_html_end_to_end(tmp_path):
    source = tmp_path / "case.html"
    output = tmp_path / "segments.json"
    source.write_text(
        "<p>THE FACTS</p><p>1. Facts.</p><p>THE LAW</p>"
        "<p>2. Reasons.</p><p>FOR THESE REASONS</p><p>Holds.</p>",
        encoding="utf-8",
    )

    rc = main(
        [
            "segment",
            "--in",
            str(source),
            "--out",
            str(output),
            "--doctype",
            "HEJUD",
            "--document-id",
            "001-test",
        ]
    )

    assert rc == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "complete"
    assert result["spine"]["schema_version"] == "hudoc-spine.v1"
    assert result["spine"]["document_id"] == "001-test"
    assert [span["section"] for span in result["spans"]] == [
        "facts",
        "the_law",
        "operative",
    ]


def test_segment_jsonl_records_missing_text_visibly(tmp_path):
    source = tmp_path / "cases.jsonl"
    output = tmp_path / "segments.jsonl"
    source.write_text(
        json.dumps(
            {
                "itemid": "001-good",
                "doctype": "HEJUD",
                "text": "THE LAW\n\n1. Reasons.\n\nFOR THESE REASONS\n\nHolds.",
            }
        )
        + "\n"
        + json.dumps({"itemid": "001-missing"})
        + "\n",
        encoding="utf-8",
    )

    assert main(["segment", "--in", str(source), "--out", str(output)]) == 0
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["sections"]["status"] == "complete"
    assert rows[1]["error"] == "missing_text_field:text"


def test_bilingual_subcommands_parse():
    parser = build_parser()

    rec = parser.parse_args(
        ["reconcile", "--in", "raw.parquet", "--out", "out.parquet", "--keep-extra-fre"]
    )
    assert rec.command == "reconcile"
    assert rec.keep_extra_fre is True

    rf = parser.parse_args(
        [
            "rescue-french",
            "--in",
            "cases.parquet",
            "--checkpoint",
            "rescue.jsonl",
            "--retry-errors",
            "--limit",
            "50",
        ]
    )
    assert rf.command == "rescue-french"
    assert rf.retry_errors is True
    assert rf.limit == 50

    cb = parser.parse_args(
        [
            "corpus",
            "build",
            "--article",
            "3",
            "--out",
            "corpus/",
            "--no-rescue",
            "--docx",
            "--page-size",
            "500",
        ]
    )
    assert cb.command == "corpus"
    assert cb.corpus_command == "build"
    assert cb.no_rescue is True
    assert cb.docx is True
    assert cb.page_size == 500


def test_fetch_case_rescue_french_flag():
    parser = build_parser()
    args = parser.parse_args(["fetch-case", "--itemid", "001-1", "--rescue-french"])
    assert args.rescue_french is True


def test_local_subcommands_parse():
    parser = build_parser()
    ls = parser.parse_args(
        ["local", "search", "text", "torture", "--data-dir", "corpus/", "--format", "json"]
    )
    assert ls.command == "local" and ls.local_command == "search"
    assert ls.mode == "text" and ls.query == "torture"

    lb = parser.parse_args(["local", "browse", "cases", "--stats"])
    assert lb.local_command == "browse"
    assert lb.table == "cases" and lb.stats is True

    le = parser.parse_args(["local", "export", "--out", "exp/", "--format", "xlsx"])
    assert le.local_command == "export" and le.format == "xlsx"

    cbk = parser.parse_args(["codebook", "--out", "CODEBOOK.md"])
    assert cbk.command == "codebook" and cbk.out == "CODEBOOK.md"


def test_filters_from_args_maps_kwargs():
    from hudoc_py.cli import _filters_from_args

    parser = build_parser()
    args = parser.parse_args(
        ["search", "--thesaurus", "350", "--concept", "Expulsion", "--separate-opinion", "false"]
    )
    filters = _filters_from_args(args)
    assert filters["kpthesaurus"] == "350"
    assert filters["concepts"] == "Expulsion"
    assert filters["separate_opinion"] is False
    assert "article" not in filters


def test_exec_count_parser():
    parser = build_parser()
    args = parser.parse_args(["exec", "count", "--state", "ITA"])
    assert args.exec_command == "count"
    assert args.state == "ITA"


def test_exec_search_parser():
    parser = build_parser()
    args = parser.parse_args(
        ["exec", "search", "--state", "ITA", "--supervision", "enhanced", "--limit", "10"]
    )
    assert args.command == "exec"
    assert args.exec_command == "search"
    assert args.state == "ITA"
    assert args.supervision == "enhanced"


def test_exec_fetch_case_positional():
    parser = build_parser()
    args = parser.parse_args(["exec", "fetch-case", "46221/99"])
    assert args.exec_command == "fetch-case"
    assert args.appno == "46221/99"


def test_versions_and_dispositive_parsers():
    parser = build_parser()
    versions = parser.parse_args(["versions", "list", "--appno", "46221/99"])
    assert versions.versions_command == "list"
    dispositive = parser.parse_args(["dispositive", "--itemid", "001-1"])
    assert dispositive.command == "dispositive"


def test_graph_parsers():
    parser = build_parser()
    args = parser.parse_args(["graph", "metrics", "--in", "c.parquet", "--out", "m.csv"])
    assert args.graph_command == "metrics"
    args = parser.parse_args(
        ["graph", "html", "--in", "c.jsonl", "--out", "g.html", "--max-nodes", "500"]
    )
    assert args.max_nodes == 500


def test_parquet_reader_restores_nulls_in_mixed_string_columns(tmp_path):
    import pandas as pd

    from hudoc_py.cli import _read_items

    path = tmp_path / "cases.parquet"
    pd.DataFrame(
        [
            {"itemid": "001-1", "casecitation": None},
            {"itemid": "001-2", "casecitation": "Example"},
        ]
    ).to_parquet(path, index=False)

    rows = _read_items(str(path))
    assert rows[0]["casecitation"] is None
    assert rows[1]["casecitation"] == "Example"
