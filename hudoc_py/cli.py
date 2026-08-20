"""``echr-py`` CLI.

Subcommands::

    echr-py search        --article 3 --respondent ITA --limit 100 --out results.parquet
    echr-py count         --article 3 --respondent ITA
    echr-py smart-fetch   --text '"positive obligations"' --top 5 --section the_law
    echr-py fetch-case    --appno 46221/99 --with-text --section dispositif --format md
    echr-py exec search   --state ITA --supervision enhanced --limit 50 --out exec.parquet
    echr-py exec count    --state ITA
    echr-py exec fetch-case  46221/99
    echr-py mcp           # start MCP stdio server for Claude Desktop
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

from . import __version__

# (CLI flag, search() kwarg) pairs shared by search / count / smart-fetch.
_FILTER_ARGS = (
    ("article", "article"),
    ("respondent", "respondent"),
    ("appno", "appno"),
    ("date_from", "date_from"),
    ("date_to", "date_to"),
    ("importance", "importance"),
    ("conclusion", "conclusion"),
    ("thesaurus", "kpthesaurus"),
    ("concept", "concepts"),
    ("docname", "docname"),
    ("body", "body"),
    ("doctype_branch", "doctypebranch"),
    ("ecli", "ecli"),
    ("text", "text"),
    ("language", "languages"),
    ("doctype", "doctypes"),
    ("collection", "collection"),
    ("hudoc_url", "hudoc_url"),
)


def _language_code(value: str) -> str:
    """Normalise and validate a HUDOC ``languageisocode`` value."""
    code = value.strip().upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise argparse.ArgumentTypeError(
            "language must be a three-letter HUDOC languageisocode, e.g. ENG or FRE"
        )
    return code


def _page_size(value: str) -> int:
    size = int(value)
    if not 1 <= size <= 500:
        raise argparse.ArgumentTypeError("page size must be between 1 and 500")
    return size


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return number


def _add_search_filters(parser: argparse.ArgumentParser) -> None:
    """Add the shared HUDOC filter flags to a subparser."""
    parser.add_argument("--article", help="Convention article, e.g. 3 or 8")
    parser.add_argument("--respondent", help="ISO country code, e.g. ITA, FRA")
    parser.add_argument("--appno", help='Application number, e.g. "46221/99"')
    parser.add_argument("--date-from", help='ISO date or "YYYY-MM-DD"')
    parser.add_argument("--date-to", help='ISO date or "YYYY-MM-DD"')
    parser.add_argument("--importance", help='Importance level "1"-"4"')
    parser.add_argument("--conclusion", help='e.g. "Violation of Article 3"')
    parser.add_argument(
        "--thesaurus", help='Keyword text (e.g. "torture") or numeric keypoint ID (350)'
    )
    parser.add_argument("--concept", help="ECHR concept label")
    parser.add_argument("--docname", help="Case title fragment, e.g. McCann")
    parser.add_argument(
        "--body", choices=["grand-chamber", "chamber", "committee"], help="Bench composition"
    )
    parser.add_argument("--doctype-branch", help="Raw doctypebranch value")
    parser.add_argument("--ecli", help="ECLI identifier")
    parser.add_argument(
        "--language",
        action="append",
        type=_language_code,
        help=("HUDOC languageisocode; repeat to select several languages (default: ENG and FRE)"),
    )
    parser.add_argument(
        "--doctype",
        action="append",
        type=str.upper,
        help="Raw HUDOC doctype; repeat for several types",
    )
    parser.add_argument(
        "--collection",
        action="append",
        help="HUDOC documentcollectionid; repeat for several collections",
    )
    parser.add_argument(
        "--hudoc-url",
        help="Shareable HUDOC browser/API URL; explicit filters are AND-ed onto it",
    )
    parser.add_argument(
        "--separate-opinion", choices=["true", "false"], help="Has separate opinion(s)"
    )
    parser.add_argument("--text", help="Free-text Lucene fragment AND-ed onto the query")


def _filters_from_args(args: argparse.Namespace) -> dict:
    """Collect non-empty filter kwargs from parsed args."""
    filters = {
        kwarg: getattr(args, attr)
        for attr, kwarg in _FILTER_ARGS
        if getattr(args, attr, None) is not None
    }
    sep = getattr(args, "separate_opinion", None)
    if sep is not None:
        filters["separate_opinion"] = sep == "true"
    return filters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echr-py",
        description="Pythonic access to HUDOC and HUDOC-EXEC.",
    )
    parser.add_argument("--version", action="version", version=f"echr-py {__version__}")
    sub = parser.add_subparsers(dest="command", required=False)

    # search ---------------------------------------------------------------
    p_search = sub.add_parser("search", help="Search HUDOC main")
    _add_search_filters(p_search)
    p_search.add_argument(
        "--sort", choices=["relevance", "date-desc", "date-asc"], default="relevance"
    )
    p_search.add_argument(
        "--page-size", type=_page_size, default=100, help="HUDOC rows requested per page (max 500)"
    )
    p_search.add_argument("--limit", type=int, default=100)
    p_search.add_argument(
        "--all", action="store_true", help="Fetch every match using checked date partitions"
    )
    p_search.add_argument(
        "--out", help="Output path (.parquet, .jsonl, .csv, or JSON). Default: stdout JSON."
    )

    # count ------------------------------------------------------------------
    p_count = sub.add_parser("count", help="Count HUDOC matches without fetching rows")
    _add_search_filters(p_count)
    p_count.add_argument("--out", help="Output JSON path. Default: stdout.")

    # smart-fetch --------------------------------------------------------------
    p_sf = sub.add_parser("smart-fetch", help="Search, keep the top-N matches, fetch their texts")
    _add_search_filters(p_sf)
    p_sf.add_argument("--top", type=int, default=10, help="Number of cases to fetch")
    p_sf.add_argument(
        "--page-size",
        type=_page_size,
        default=100,
        help="HUDOC metadata rows requested per page (max 500)",
    )
    p_sf.add_argument(
        "--concurrency",
        type=_positive_int,
        default=20,
        help="Maximum concurrent full-text downloads (default: 20)",
    )
    p_sf.add_argument("--sort", choices=["relevance", "date-desc", "date-asc"], default="relevance")
    p_sf.add_argument(
        "--format", choices=["text", "md", "html"], default="text", help="Text format"
    )
    p_sf.add_argument(
        "--section",
        choices=["full", "the_law", "dispositif"],
        default="full",
        help="Keep only this section of each text",
    )
    p_sf.add_argument("--no-text", action="store_true", help="Skip text download")
    p_sf.add_argument(
        "--rich-sections",
        action="store_true",
        help="Build the source-aware block spine and rich canonical sections",
    )
    p_sf.add_argument("--docx-dir", help="Also download each case's DOCX into this directory.")
    p_sf.add_argument("--out", help="Output path (.parquet or .jsonl). Default: stdout JSON.")

    # fetch-case -----------------------------------------------------------
    p_fc = sub.add_parser("fetch-case", help="Fetch a single case (main)")
    p_fc.add_argument("--appno", help='Application number, e.g. "46221/99"')
    p_fc.add_argument("--itemid", help='HUDOC itemid, e.g. "001-94054"')
    p_fc.add_argument(
        "--language",
        type=_language_code,
        help=(
            "Preferred HUDOC languageisocode when selecting by application number, "
            "e.g. ENG or FRE; an item ID already identifies one language record"
        ),
    )
    p_fc.add_argument("--with-text", action="store_true", help="Fetch the full text")
    p_fc.add_argument(
        "--rich-sections",
        action="store_true",
        help="Build the source-aware block spine and rich canonical sections",
    )
    p_fc.add_argument(
        "--format", choices=["text", "md", "html"], default="text", help="Text format"
    )
    p_fc.add_argument(
        "--section",
        choices=["full", "the_law", "dispositif"],
        default="full",
        help="Section to print (requires --with-text)",
    )
    p_fc.add_argument("--docx", help="Also download the raw DOCX to this path.")
    p_fc.add_argument(
        "--rescue-french",
        action="store_true",
        help="If the case has no text, find its French sibling live.",
    )
    p_fc.add_argument("--out", help="Output JSON path. Default: stdout.")

    # versions -------------------------------------------------------------
    p_versions = sub.add_parser("versions", help="Discover or download all language versions")
    versions_sub = p_versions.add_subparsers(dest="versions_command")
    for name in ("list", "download"):
        p_version = versions_sub.add_parser(name)
        identity = p_version.add_mutually_exclusive_group(required=True)
        identity.add_argument("--appno")
        identity.add_argument("--ecli")
        p_version.add_argument("--language", action="append", type=_language_code)
        if name == "download":
            p_version.add_argument("--out", required=True, help="Acquisition directory")
            p_version.add_argument(
                "--formats", default="html,txt,md,docx", help="Comma-separated formats"
            )
            p_version.add_argument("--concurrency", type=int, default=10)

    p_disp = sub.add_parser("dispositive", help="Retrieve individual operative-part rulings")
    identity = p_disp.add_mutually_exclusive_group(required=True)
    identity.add_argument("--appno")
    identity.add_argument("--itemid")
    p_disp.add_argument("--language", type=_language_code)
    p_disp.add_argument("--out")

    # segment --------------------------------------------------------------
    p_segment = sub.add_parser(
        "segment", help="Segment local HUDOC HTML/text or a table of document records"
    )
    p_segment.add_argument(
        "--in",
        dest="input",
        required=True,
        help="Input .html/.txt/.json/.jsonl/.csv/.parquet",
    )
    p_segment.add_argument("--out", help="Output JSON/JSONL/CSV/Parquet; default stdout")
    p_segment.add_argument(
        "--format",
        choices=["auto", "html", "text"],
        default="auto",
        help="Input content format (auto uses extension/content)",
    )
    p_segment.add_argument("--text-field", default="text")
    p_segment.add_argument("--document-id-field", default="itemid")
    p_segment.add_argument("--doctype-field", default="doctype")
    p_segment.add_argument("--doctype-branch-field", default="doctype_branch")
    p_segment.add_argument("--doctype", help="Static doctype for a single file")
    p_segment.add_argument("--doctype-branch", help="Static doctype branch for a single file")
    p_segment.add_argument("--document-id", help="Static document ID for a single file")

    # exec -----------------------------------------------------------------
    p_exec = sub.add_parser("exec", help="HUDOC-EXEC subcommands")
    exec_sub = p_exec.add_subparsers(dest="exec_command")

    p_es = exec_sub.add_parser("search", help="Search HUDOC-EXEC cases")
    p_es.add_argument("--state", help="ISO country code")
    p_es.add_argument("--supervision", choices=["standard", "enhanced"])
    p_es.add_argument(
        "--closed", choices=["true", "false"], help='Filter by execisclosed ("true"/"false")'
    )
    p_es.add_argument("--case-type", help='e.g. "leading" or "repetitive"')
    p_es.add_argument("--limit", type=int, default=100)
    p_es.add_argument("--out", help="Output path (.parquet or .jsonl). Default: stdout JSON.")

    p_ec = exec_sub.add_parser("count", help="Count HUDOC-EXEC cases without fetching rows")
    p_ec.add_argument("--state", help="ISO country code")
    p_ec.add_argument("--supervision", choices=["standard", "enhanced"])
    p_ec.add_argument(
        "--closed", choices=["true", "false"], help='Filter by execisclosed ("true"/"false")'
    )
    p_ec.add_argument("--case-type", help='e.g. "leading" or "repetitive"')
    p_ec.add_argument("--out", help="Output JSON path. Default: stdout.")

    p_esd = exec_sub.add_parser(
        "search-documents", help="Search official HUDOC-EXEC source documents"
    )
    p_esd.add_argument(
        "--collection",
        required=True,
        help="Official collection code, e.g. acp, acr, CMDEC, ngo, or EXECUTION",
    )
    p_esd.add_argument("--state", help="ISO country code")
    p_esd.add_argument("--appno", help='Application number, e.g. "46221/99"')
    p_esd.add_argument("--master-group-id", help="Official HUDOC-EXEC master-group ID")
    p_esd.add_argument("--language", type=_language_code, default="ENG")
    p_esd.add_argument("--limit", type=int, default=100)
    p_esd.add_argument("--page-size", type=_page_size)
    p_esd.add_argument(
        "--out", help="Output path (.parquet or .jsonl). Default: stdout JSON."
    )

    p_efc = exec_sub.add_parser("fetch-case", help="Fetch a single execution case")
    p_efc.add_argument("appno", help='Application number, e.g. "46221/99"')
    p_efc.add_argument("--no-documents", action="store_true", help="Skip linked documents")
    p_efc.add_argument("--out", help="Output JSON path. Default: stdout.")

    p_eraw = exec_sub.add_parser("download-raw", help="Download raw EXEC PDFs with resume")
    p_eraw.add_argument("--in", dest="input", required=True, help="Execution documents JSONL")
    p_eraw.add_argument("--out", required=True, help="Output directory")
    p_eraw.add_argument("--manifest", help="Manifest JSONL (default: OUT/manifest.jsonl)")
    p_eraw.add_argument("--concurrency", type=int, default=10)
    p_eraw.add_argument("--extract-text", action="store_true")
    p_eraw.add_argument("--ocr", action="store_true")
    p_eraw.add_argument("--no-resume", action="store_true")

    p_etext = exec_sub.add_parser("extract-text", help="Convert a raw EXEC document locally")
    p_etext.add_argument("--in", dest="input", required=True)
    p_etext.add_argument("--out", required=True)
    p_etext.add_argument("--type", choices=["pdf", "docx", "html"])
    p_etext.add_argument("--markdown", action="store_true")
    p_etext.add_argument("--ocr", action="store_true")

    p_escrape = exec_sub.add_parser("scrape-related", help="Browser fallback for Related documents")
    p_escrape.add_argument("case_ids", nargs="+")
    p_escrape.add_argument("--out", required=True, help="Checkpoint/output JSONL")
    p_escrape.add_argument("--concurrency", type=int, default=3)
    p_escrape.add_argument("--headed", action="store_true")
    p_escrape.add_argument("--no-resume", action="store_true")

    # study ---------------------------------------------------------------
    p_study = sub.add_parser("study", help="Run bounded, reproducible research studies")
    study_sub = p_study.add_subparsers(dest="study_command")
    p_study_init = study_sub.add_parser(
        "init", help="Create an explicit optional study specification"
    )
    p_study_init.add_argument("template", choices=["citation-use"])
    p_study_init.add_argument("--source", required=True, help="Occurrence JSONL or Parquet")
    p_study_init.add_argument("--provider", required=True)
    p_study_init.add_argument("--model", required=True)
    taxonomy_group = p_study_init.add_mutually_exclusive_group()
    taxonomy_group.add_argument(
        "--taxonomy", choices=["minimal", "multiaxial"], default="multiaxial"
    )
    taxonomy_group.add_argument("--schema", help="User-owned JSON Schema")
    p_study_init.add_argument("--out", required=True, help="Study YAML path")
    for name in ("validate", "plan", "run"):
        p_value = study_sub.add_parser(name)
        p_value.add_argument("spec", help="Versioned study YAML")
        if name in {"plan", "run"}:
            p_value.add_argument("--out", required=True, help="Run or plan output directory")
        if name == "run":
            p_value.add_argument("--no-resume", action="store_true")
            p_value.add_argument("--wait", action="store_true")
            p_value.add_argument("--poll-interval", type=float, default=30.0)
            p_value.add_argument("--max-polls", type=int)
    p_study_status = study_sub.add_parser("status")
    p_study_status.add_argument("run", help="Run directory or manifest.json")
    p_study_resume = study_sub.add_parser("resume")
    p_study_resume.add_argument("run", help="Run directory")
    p_study_resume.add_argument("--wait", action="store_true")
    p_study_resume.add_argument("--poll-interval", type=float, default=30.0)
    p_study_resume.add_argument("--max-polls", type=int)
    p_study_cancel = study_sub.add_parser("cancel")
    p_study_cancel.add_argument("run", help="Run directory")
    p_study_export = study_sub.add_parser("export")
    p_study_export.add_argument("run", help="Run directory")
    p_study_export.add_argument("--out", required=True)
    p_study_export.add_argument("--format", choices=["jsonl", "parquet"], required=True)
    p_study_bench = study_sub.add_parser("benchmark-retrieval")
    p_study_bench.add_argument("--database", required=True)
    p_study_bench.add_argument("--qrels", required=True)
    p_study_bench.add_argument(
        "--mode", choices=["lexical", "semantic", "hybrid"], default="hybrid"
    )
    p_study_bench.add_argument("--embeddings")
    p_study_bench.add_argument("--top-k", type=int, default=25)
    p_study_bench.add_argument("--candidate-k", type=int, default=100)
    p_study_bench.add_argument("--out", required=True)

    # embeddings ----------------------------------------------------------
    p_embeddings = sub.add_parser("embeddings", help="Build and verify portable embeddings")
    embeddings_sub = p_embeddings.add_subparsers(dest="embeddings_command")
    p_embeddings_build = embeddings_sub.add_parser("build")
    p_embeddings_build.add_argument("--database", required=True)
    p_embeddings_build.add_argument("--out", required=True)
    p_embeddings_build.add_argument("--provider", required=True)
    p_embeddings_build.add_argument("--model", required=True)
    p_embeddings_build.add_argument(
        "--model-revision",
        help="Exact local Sentence Transformers revision (normally a commit SHA)",
    )
    p_embeddings_build.add_argument("--batch-size", type=int, default=128)
    p_embeddings_build.add_argument("--query-prompt", default="")
    p_embeddings_build.add_argument("--document-prompt", default="")
    p_embeddings_build.add_argument(
        "--section",
        action="append",
        dest="sections",
        help="Embed only this section (repeatable); omitted embeds every paragraph",
    )
    p_embeddings_build.add_argument(
        "--include-footnotes",
        action="store_true",
        help="Also embed linked footnote bodies (requires paragraph index v3)",
    )
    p_embeddings_build.add_argument("--accelerator", choices=["exact", "faiss"], default="exact")
    p_embeddings_verify = embeddings_sub.add_parser("verify")
    p_embeddings_verify.add_argument("--in", dest="input", required=True)
    p_embeddings_verify.add_argument("--database")

    # citations ------------------------------------------------------------
    p_citations = sub.add_parser("citations", help="Authoritative SCL citation resolution")
    citations_sub = p_citations.add_subparsers(dest="citations_command")

    p_cauth = citations_sub.add_parser("authority", help="Manage the official citation authority")
    authority_sub = p_cauth.add_subparsers(dest="authority_command")
    p_cauth_import = authority_sub.add_parser("import", help="Import the Court's official PDF")
    p_cauth_import.add_argument("--pdf", required=True, help="Locally downloaded official PDF")
    p_cauth_import.add_argument("--out", required=True, help="Authority output directory")
    p_cauth_import.add_argument("--updated-through", help="Edition date, e.g. 2026-06-26")
    p_cauth_import.add_argument("--language", choices=["eng", "fra"], default="eng")
    p_cauth_import.add_argument("--source-url")
    p_cauth_import.add_argument(
        "--retrieved-at",
        help="Pinned ISO-8601 retrieval timestamp for byte-reproducible authority artifacts",
    )
    p_cauth_merge = authority_sub.add_parser("merge", help="Merge official language editions")
    p_cauth_merge.add_argument("--authority", action="append", required=True)
    p_cauth_merge.add_argument("--out", required=True)

    p_cbenchmark = citations_sub.add_parser(
        "benchmark", help="Fetch, import, and compare external citation reference sets"
    )
    benchmark_sub = p_cbenchmark.add_subparsers(dest="benchmark_command")
    p_cbenchmark_fetch = benchmark_sub.add_parser("fetch")
    p_cbenchmark_fetch.add_argument("kind", choices=["mumford", "ecthr-pcr"])
    p_cbenchmark_fetch.add_argument("--out", required=True)
    p_cbenchmark_fetch.add_argument("--revision")
    p_cbenchmark_fetch.add_argument("--sha256")
    p_cbenchmark_fetch.add_argument("--timeout", type=float, default=60.0)
    p_cbenchmark_fetch.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    p_cbenchmark_import = benchmark_sub.add_parser("import")
    p_cbenchmark_import.add_argument("--kind", choices=["mumford", "ecthr-pcr"], required=True)
    p_cbenchmark_import.add_argument("--source", required=True)
    p_cbenchmark_import.add_argument("--out", required=True)
    p_cbenchmark_compare = benchmark_sub.add_parser("compare")
    p_cbenchmark_compare.add_argument("--kind", choices=["mumford", "ecthr-pcr"], required=True)
    p_cbenchmark_compare.add_argument("--reference", required=True)
    p_cbenchmark_compare.add_argument("--local", required=True)
    p_cbenchmark_compare.add_argument("--labels")
    p_cbenchmark_compare.add_argument(
        "--documents",
        help=(
            "Imported Mumford documents.jsonl; projects full HUDOC occurrence "
            "offsets into the XMI Sofa coordinate system"
        ),
    )
    p_cbenchmark_compare.add_argument(
        "--reference-scope",
        choices=["all", "echr"],
        default="all",
        help="Evaluate every curated label or only Mumford's ECHR case-law labels",
    )
    p_cbenchmark_compare.add_argument(
        "--projected-out",
        help="Optional JSONL/Parquet audit table of projected full-pipeline occurrences",
    )
    p_cbenchmark_compare.add_argument("--out", required=True)

    p_ccatalog = citations_sub.add_parser(
        "catalog", help="Build or verify the offline historical reporter catalog"
    )
    catalog_sub = p_ccatalog.add_subparsers(dest="catalog_command")
    p_ccatalog_build = catalog_sub.add_parser("build", help="Build a versioned catalog")
    p_ccatalog_build.add_argument("--out", required=True)
    p_ccatalog_build.add_argument("--in", dest="input", help="Optional HUDOC metadata")
    p_ccatalog_build.add_argument("--authority", help="Authority JSON or directory")
    p_ccatalog_verify = catalog_sub.add_parser("verify", help="Verify catalog checksum")
    p_ccatalog_verify.add_argument("--in", dest="input", required=True)

    p_cresolve = citations_sub.add_parser(
        "resolve", help="Resolve SCL references to exact documents"
    )
    p_cresolve.add_argument("--in", dest="input", required=True, help="Source cases Parquet/JSONL")
    p_cresolve.add_argument("--out", required=True, help="Resolution artifact directory")
    p_cresolve.add_argument("--authority", help="Authority JSON or directory; default: packaged")
    p_cresolve.add_argument("--catalog", help="Optional local HUDOC metadata Parquet/JSONL")
    p_cresolve.add_argument("--overrides", help="Reviewed override CSV")
    p_cresolve.add_argument("--offline", action="store_true", help="Do not query HUDOC")

    p_clocate = citations_sub.add_parser(
        "locate", help="Locate SCL authorities in source paragraphs"
    )
    p_clocate.add_argument("--in", dest="input", required=True, help="Source cases Parquet/JSONL")
    p_clocate.add_argument(
        "--resolution-dir", required=True, help="Existing authoritative resolution artifacts"
    )
    p_clocate.add_argument("--out", required=True, help="Occurrence artifact directory")
    p_clocate.add_argument("--html-dir", help="HUDOC HTML cache; default: <out>/source-html")
    p_clocate.add_argument(
        "--offline", action="store_true", help="Require every source HTML file in the cache"
    )
    p_clocate.add_argument(
        "--scope",
        choices=("scl", "inclusive"),
        default="inclusive",
        help="SCL-only compatibility output or deterministic full-text discovery",
    )
    p_clocate.add_argument(
        "--resolve-paragraphs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resolve owned pinpoints against cited-document paragraph spines (default: on)",
    )
    p_clocate.add_argument(
        "--target-html-dir",
        help="Cited-document HTML cache; default: <out>/target-html",
    )

    p_creview = citations_sub.add_parser("review", help="Generate ambiguity review HTML and CSV")
    p_creview.add_argument("--resolution-dir", required=True)
    p_creview.add_argument("--out", required=True, help="Review HTML")
    p_creview.add_argument("--csv", required=True, help="Override CSV template")

    # graph ----------------------------------------------------------------
    p_graph = sub.add_parser("graph", help="Citation-graph metrics and viz")
    graph_sub = p_graph.add_subparsers(dest="graph_command")

    def _add_graph_input(p: argparse.ArgumentParser) -> None:
        inputs = p.add_mutually_exclusive_group(required=True)
        inputs.add_argument("--in", dest="input", help="Cases file for strict offline resolution")
        inputs.add_argument("--citations", help="Resolved citation artifact directory")
        p.add_argument(
            "--use-extracted-appno", action="store_true", help="Deprecated unsafe legacy fallback"
        )
        p.add_argument(
            "--allow-incomplete",
            action="store_true",
            help="Diagnostic output only; never measurement-grade",
        )

    p_gm = graph_sub.add_parser("metrics", help="Centrality metrics table")
    _add_graph_input(p_gm)
    p_gm.add_argument("--out", required=True, help="Output (.parquet or .csv)")
    p_gm.add_argument("--weight", choices=["binary", "citation_count"], default="binary")

    p_gh = graph_sub.add_parser("html", help="Interactive D3 network viewer")
    _add_graph_input(p_gh)
    p_gh.add_argument("--out", required=True, help="Output .html path")
    p_gh.add_argument("--max-nodes", type=int, default=None)

    p_gg = graph_sub.add_parser("gexf", help="Gephi-compatible citation graph")
    _add_graph_input(p_gg)
    p_gg.add_argument("--out", required=True, help="Output .gexf path")
    p_gg.add_argument("--weight", choices=["binary", "citation_count"], default="binary")
    p_gg.add_argument("--no-metrics", action="store_true", help="Omit centrality attributes")

    p_ge = graph_sub.add_parser("export", help="Unified JSON/GEXF/offline-HTML export")
    p_ge.add_argument(
        "--kind",
        required=True,
        choices=["citation-scl", "citation-inclusive", "citation-paragraph", "networkx"],
    )
    p_ge.add_argument("--in", dest="input", required=True)
    p_ge.add_argument("--out", required=True)
    p_ge.add_argument("--format", choices=["json", "gexf", "html"], required=True)
    p_ge.add_argument("--max-nodes", type=int)
    p_ge.add_argument("--allow-incomplete", action="store_true")
    p_ge.add_argument(
        "--source-item", action="append", default=[], help="Keep paragraph edges from item ID"
    )
    p_ge.add_argument(
        "--source-component",
        action="append",
        default=[],
        choices=["majority", "opinion", "appendix"],
        help="Keep paragraph edges from this source component",
    )
    p_ge.add_argument(
        "--opinion-id", action="append", default=[], help="Keep paragraph edges from opinion ID"
    )
    p_ge.add_argument(
        "--footnotes-only", action="store_true", help="Keep only citations printed in footnotes"
    )

    # reconcile ------------------------------------------------------------
    p_rec = sub.add_parser("reconcile", help="ENG/FRE reconcile a cases file")
    p_rec.add_argument("--in", dest="input", required=True, help="Cases file (.parquet or .jsonl)")
    p_rec.add_argument("--out", required=True, help="Reconciled cases (.parquet or .jsonl)")
    p_rec.add_argument("--duplicates", help="Optional path for removed rows")
    p_rec.add_argument("--stats", help="Optional path for the ReconcileStats JSON")
    p_rec.add_argument(
        "--keep-extra-fre",
        action="store_true",
        help="Keep extra French rows as duplicate primaries (parity mode)",
    )

    # rescue-french --------------------------------------------------------
    p_rf = sub.add_parser("rescue-french", help="Find French siblings for placeholder cases")
    p_rf.add_argument("--in", dest="input", required=True, help="Cases file (.parquet or .jsonl)")
    p_rf.add_argument("--checkpoint", required=True, help="Rescue checkpoint JSONL (resumable)")
    p_rf.add_argument("--out", help="Optional path to write cases with french_itemid set")
    p_rf.add_argument("--csv", help="Optional eng_itemid,french_itemid,appno CSV export")
    p_rf.add_argument("--download-texts", help="Download French sibling texts into this dir")
    p_rf.add_argument("--retry-errors", action="store_true")
    p_rf.add_argument("--no-resume", action="store_true")
    p_rf.add_argument("--limit", type=int, default=None)
    p_rf.add_argument("--concurrency", type=int, default=None)

    # corpus ---------------------------------------------------------------
    p_corpus = sub.add_parser("corpus", help="Corpus build pipeline")
    corpus_sub = p_corpus.add_subparsers(dest="corpus_command")
    p_cb = corpus_sub.add_parser("build", help="Search → reconcile → rescue → texts → save")
    _add_search_filters(p_cb)
    p_cb.add_argument("--out", required=True, help="Output directory")
    p_cb.add_argument(
        "--selection",
        help="Immutable item ID/ECLI selection (.json, .jsonl, .csv, or text)",
    )
    p_cb.add_argument("--limit", type=int, default=None)
    p_cb.add_argument(
        "--page-size", type=_page_size, default=100, help="HUDOC rows requested per page (max 500)"
    )
    p_cb.add_argument("--format", choices=["text", "md", "html"], default="text")
    p_cb.add_argument("--no-rescue", action="store_true")
    p_cb.add_argument("--no-texts", action="store_true")
    p_cb.add_argument(
        "--rich-sections",
        action="store_true",
        help="Write spine, paragraph, section, opinion, bench, footnote, and dispositive tables",
    )
    p_cb.add_argument("--docx", action="store_true", help="Also download raw DOCX files")
    p_cb.add_argument("--keep-extra-fre", action="store_true")
    p_cb.add_argument(
        "--citations",
        action="store_true",
        help="Resolve one-hop citations and write graph-grade artifacts",
    )
    p_cb.add_argument("--citation-authority", help="Authority JSON/directory")
    p_cb.add_argument("--citation-overrides", help="Reviewed override CSV")

    p_cv = corpus_sub.add_parser("validate", help="Validate a hudoc-corpus/v1 directory")
    p_cv.add_argument("--in", dest="input", required=True, help="Corpus directory")
    p_cv.add_argument("--out", required=True, help="Validation report JSON")

    p_cp = corpus_sub.add_parser("package", help="Create a deterministic corpus ZIP")
    p_cp.add_argument("--in", dest="input", required=True, help="Corpus directory")
    p_cp.add_argument("--out", required=True, help="Output ZIP or distribution directory")

    # local ----------------------------------------------------------------
    p_local = sub.add_parser("local", help="Offline search/browse over a built corpus")
    local_sub = p_local.add_subparsers(dest="local_command")

    p_ls = local_sub.add_parser("search", help="Search a local corpus")
    p_ls.add_argument("mode", choices=["text", "party", "citing", "cited-by", "list"])
    p_ls.add_argument("query")
    p_ls.add_argument("--data-dir", help="Corpus directory (default: ~/.echr-py/data)")
    p_ls.add_argument("--limit", type=int, default=50)
    p_ls.add_argument("--format", choices=["table", "csv", "json"], default="table")
    p_ls.add_argument("--date-from")
    p_ls.add_argument("--date-to")
    p_ls.add_argument("--respondent")

    p_lb = local_sub.add_parser("browse", help="Browse local corpus tables")
    p_lb.add_argument("table", nargs="?", help="Table name (omit to list all)")
    p_lb.add_argument("--data-dir")
    p_lb.add_argument("--stats", action="store_true")
    p_lb.add_argument("--columns", action="store_true")
    p_lb.add_argument("--limit", type=int, default=20)
    p_lb.add_argument("--format", choices=["table", "csv", "json"], default="table")

    p_le = local_sub.add_parser("export", help="Export corpus tables to CSV/XLSX")
    p_le.add_argument("--data-dir")
    p_le.add_argument("--out", required=True, help="Output directory")
    p_le.add_argument("--format", choices=["csv", "xlsx"], default="csv")
    p_le.add_argument("--tables", help="Comma-separated table names (default: all)")

    p_li = local_sub.add_parser("index-paragraphs", help="Build a SQLite FTS paragraph index")
    p_li.add_argument("--data-dir", required=True)
    p_li.add_argument("--database", required=True)

    p_lp = local_sub.add_parser("paragraphs", help="Retrieve or search indexed paragraphs")
    p_lp.add_argument("--database", required=True)
    p_lp.add_argument("query", nargs="?")
    p_lp.add_argument("--itemid")
    p_lp.add_argument("--para-id")
    p_lp.add_argument("--section")
    p_lp.add_argument("--language", type=_language_code)
    p_lp.add_argument("--mode", choices=["lexical", "semantic", "hybrid"], default="lexical")
    p_lp.add_argument("--embeddings")
    p_lp.add_argument("--candidate-k", type=int, default=100)
    p_lp.add_argument("--source-component", choices=["majority", "opinion", "appendix"])
    p_lp.add_argument("--opinion-id")
    p_lp.add_argument("--date-from")
    p_lp.add_argument("--date-to")
    p_lp.add_argument("--limit", type=int, default=25)
    p_lp.add_argument("--out")

    # codebook -------------------------------------------------------------
    p_cbk = sub.add_parser("codebook", help="Write a CODEBOOK.md for the corpus tables")
    p_cbk.add_argument("--out", default="CODEBOOK.md", help="Output CODEBOOK.md path")

    # keypoints ------------------------------------------------------------
    p_kp = sub.add_parser("keypoints", help="Look up ECHR keyword (kpthesaurus) IDs by text")
    p_kp.add_argument("query", help='Keyword text, e.g. "torture"')

    # gui ------------------------------------------------------------------
    sub.add_parser("gui", help="Launch the local Streamlit browser (needs the 'gui' extra)")

    # mcp ------------------------------------------------------------------
    p_mcp = sub.add_parser("mcp", help="Start the read-only MCP stdio server")
    p_mcp.add_argument("--enable-jobs", action="store_true")
    p_mcp.add_argument("--job-root")
    p_mcp.add_argument("--allow-input-root", action="append", default=[])
    p_mcp.add_argument("--allow-provider", action="append", default=[])
    p_mcp.add_argument("--allow-study", action="append", default=[])
    p_mcp.add_argument("--pricing-file")
    p_mcp.add_argument("--max-job-budget-usd", type=float)
    p_mcp.add_argument("--job-workers", type=int, default=2)

    return parser


def _write_records(records: list[dict], out: str | None) -> None:
    if not out:
        json.dump(records, sys.stdout, indent=2, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        import pandas as pd

        pd.DataFrame(records).to_parquet(path, index=False)
    elif path.suffix == ".csv":
        import pandas as pd

        pd.DataFrame(records).to_csv(path, index=False)
    elif path.suffix == ".jsonl":
        with path.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    else:
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str))


def _write_object(obj: dict, out: str | None) -> None:
    if not out:
        json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
        return
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_search(args: argparse.Namespace) -> int:
    from . import search

    cases = search(
        sort=args.sort,
        limit=None if args.all else args.limit,
        page_size=args.page_size,
        **_filters_from_args(args),
    )
    records = [c.model_dump(mode="json") for c in cases]
    _write_records(records, args.out)
    total = f" of {cases.result_count}" if cases.result_count is not None else ""
    print(f"echr-py: {len(records)}{total} cases", file=sys.stderr)
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    from . import count

    n = count(**_filters_from_args(args))
    if args.out:
        _write_object({"count": n}, args.out)
    else:
        print(n)
    return 0


def cmd_smart_fetch(args: argparse.Namespace) -> int:
    from . import smart_fetch

    cases = smart_fetch(
        top=args.top,
        page_size=args.page_size,
        sort=args.sort,
        with_text=not args.no_text,
        text_format=args.format,
        rich_sections=args.rich_sections,
        concurrency=args.concurrency,
        **_filters_from_args(args),
    )
    records = []
    for case in cases:
        payload = case.model_dump(mode="json")
        if args.section != "full" and case.sections:
            payload["text"] = getattr(case.sections, args.section, None)
        records.append(payload)
    _write_records(records, args.out)

    if args.docx_dir:
        from pathlib import Path

        from . import fetch_docx

        docx_dir = Path(args.docx_dir)
        docx_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for case in cases:
            if case.itemid and fetch_docx(case.itemid, out=docx_dir / f"{case.itemid}.docx"):
                saved += 1
        print(f"echr-py: {saved} DOCX files saved to {docx_dir}", file=sys.stderr)

    print(f"echr-py: {len(records)} cases fetched", file=sys.stderr)
    return 0


def cmd_fetch_case(args: argparse.Namespace) -> int:
    from . import fetch_case

    if not (args.appno or args.itemid):
        print("echr-py: provide --appno or --itemid", file=sys.stderr)
        return 2
    case = fetch_case(
        appno=args.appno,
        itemid=args.itemid,
        language=args.language,
        with_text=args.with_text,
        text_format=args.format,
        segment=True,
        rich_sections=args.rich_sections,
        rescue=args.rescue_french,
        docx_out=args.docx,
    )
    if case is None:
        print("echr-py: not found", file=sys.stderr)
        return 1
    if args.docx:
        print(f"echr-py: DOCX saved to {args.docx}", file=sys.stderr)

    payload = case.model_dump(mode="json")
    if args.with_text and args.section != "full" and case.sections:
        payload["text"] = getattr(case.sections, args.section, None)
    _write_object(payload, args.out)
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    from . import download_versions, list_versions

    if args.versions_command == "list":
        versions = list_versions(appno=args.appno, ecli=args.ecli)
        wanted = {value.upper() for value in args.language or ()}
        if wanted:
            versions = [value for value in versions if value.language in wanted]
        _write_records([value.model_dump(mode="json") for value in versions], None)
        return 0
    if args.versions_command == "download":
        manifest = download_versions(
            args.out,
            appno=args.appno,
            ecli=args.ecli,
            languages=args.language,
            formats=[value.strip() for value in args.formats.split(",") if value.strip()],
            concurrency=args.concurrency,
        )
        print(
            f"echr-py versions: {len(manifest.versions)} versions → {args.out}/manifest.json",
            file=sys.stderr,
        )
        return 0 if not manifest.failures else 1
    print("echr-py versions: choose 'list' or 'download'", file=sys.stderr)
    return 2


def cmd_dispositive(args: argparse.Namespace) -> int:
    from . import fetch_case
    from .text import extract_dispositive_paragraphs

    case = fetch_case(
        appno=args.appno,
        itemid=args.itemid,
        language=args.language,
        with_text=True,
        rich_sections=True,
    )
    if case is None or case.sections is None:
        print("echr-py dispositive: document or text not found", file=sys.stderr)
        return 1
    payload = {
        "itemid": case.itemid,
        "language": case.language,
        "rulings": [
            value.model_dump(mode="json") for value in extract_dispositive_paragraphs(case.sections)
        ],
    }
    _write_object(payload, args.out)
    return 0


def _read_segmentation_records(path: Path) -> list[dict]:
    if path.suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(path).to_dict("records")
    if path.suffix == ".csv":
        import pandas as pd

        return pd.read_csv(path).to_dict("records")
    if path.suffix == ".jsonl":
        from .utils.jsonl import iter_jsonl

        return list(iter_jsonl(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(record, dict) for record in value):
        return value
    raise ValueError(f"Expected a JSON object or list of objects in {path}")


def cmd_segment(args: argparse.Namespace) -> int:
    from .text import segment_full, segment_html

    path = Path(args.input)
    if path.suffix.lower() in {".html", ".htm", ".txt"}:
        content = path.read_text(encoding="utf-8")
        is_html = args.format == "html" or (
            args.format == "auto" and path.suffix.lower() in {".html", ".htm"}
        )
        sections = (
            segment_html(
                content,
                doctype=args.doctype,
                doctype_branch=args.doctype_branch,
                document_id=args.document_id or path.stem,
            )
            if is_html
            else segment_full(
                content,
                doctype=args.doctype,
                doctype_branch=args.doctype_branch,
                document_id=args.document_id or path.stem,
            )
        )
        _write_object(sections.model_dump(mode="json"), args.out)
        return 0

    output: list[dict] = []
    for record in _read_segmentation_records(path):
        record_text = record.get(args.text_field)
        if not isinstance(record_text, str) or not record_text.strip():
            output.append(
                {
                    "document_id": record.get(args.document_id_field),
                    "error": f"missing_text_field:{args.text_field}",
                }
            )
            continue
        document_id = args.document_id or record.get(args.document_id_field)
        doctype = args.doctype or record.get(args.doctype_field)
        doctype_branch = args.doctype_branch or record.get(args.doctype_branch_field)
        is_html = args.format == "html" or (
            args.format == "auto" and bool(re.search(r"<(?:p|div|h[1-6])\b", record_text, re.I))
        )
        sections = (
            segment_html(
                record_text,
                doctype=str(doctype) if doctype else None,
                doctype_branch=str(doctype_branch) if doctype_branch else None,
                document_id=str(document_id) if document_id else None,
            )
            if is_html
            else segment_full(
                record_text,
                doctype=str(doctype) if doctype else None,
                doctype_branch=str(doctype_branch) if doctype_branch else None,
                document_id=str(document_id) if document_id else None,
            )
        )
        output.append(
            {
                "document_id": document_id,
                "sections": sections.model_dump(mode="json"),
                "error": None,
            }
        )
    _write_records(output, args.out)
    return 0


def cmd_exec_search(args: argparse.Namespace) -> int:
    from .execution import search as exec_search

    is_closed: bool | None = None
    if args.closed == "true":
        is_closed = True
    elif args.closed == "false":
        is_closed = False

    cases = exec_search(
        state=args.state,
        supervision=args.supervision,
        is_closed=is_closed,
        case_type=args.case_type,
        limit=args.limit,
    )
    records = [c.model_dump(mode="json") for c in cases]
    _write_records(records, args.out)
    print(f"echr-py exec: {len(records)} cases", file=sys.stderr)
    return 0


def cmd_exec_count(args: argparse.Namespace) -> int:
    from .execution import count as exec_count

    is_closed: bool | None = None
    if args.closed == "true":
        is_closed = True
    elif args.closed == "false":
        is_closed = False

    n = exec_count(
        state=args.state,
        supervision=args.supervision,
        is_closed=is_closed,
        case_type=args.case_type,
    )
    if args.out:
        _write_object({"count": n}, args.out)
    else:
        print(n)
    return 0


def cmd_exec_search_documents(args: argparse.Namespace) -> int:
    from .execution import search_documents

    documents = search_documents(
        collection=args.collection,
        state=args.state,
        appno=args.appno,
        master_group_id=args.master_group_id,
        language=args.language,
        limit=args.limit,
        page_size=args.page_size,
    )
    records = [document.model_dump(mode="json") for document in documents]
    _write_records(records, args.out)
    print(f"echr-py exec: {len(records)} source documents", file=sys.stderr)
    return 0


def cmd_exec_fetch_case(args: argparse.Namespace) -> int:
    from .execution import fetch_case as exec_fetch_case

    case = exec_fetch_case(args.appno, with_documents=not args.no_documents)
    if case is None:
        print("echr-py exec: not found", file=sys.stderr)
        return 1
    _write_object(case.model_dump(mode="json"), args.out)
    return 0


def cmd_exec_download_raw(args: argparse.Namespace) -> int:
    from .execution.raw_download import AsyncExecRawDownloader

    downloader = AsyncExecRawDownloader(args.out, manifest_path=args.manifest)
    stats = _aio_run(
        downloader.download(
            _read_items(args.input),
            concurrency=args.concurrency,
            resume=not args.no_resume,
            extract=args.extract_text,
            ocr=args.ocr,
        )
    )
    _write_object(stats, None)
    return 0 if stats["failed"] == 0 else 1


def cmd_exec_extract_text(args: argparse.Namespace) -> int:
    from .execution.textract import extract_text

    source = Path(args.input)
    text = extract_text(
        source, file_type=args.type or source.suffix, markdown=args.markdown, ocr=args.ocr
    )
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return 0


def cmd_exec_scrape_related(args: argparse.Namespace) -> int:
    from .execution.related_scraper import RelatedTabScraper

    async def run():
        async with RelatedTabScraper(
            concurrency=args.concurrency, headless=not args.headed
        ) as scraper:
            return await scraper.scrape_batch(
                args.case_ids, checkpoint_path=args.out, resume=not args.no_resume
            )

    results = _aio_run(run())
    print(f"echr-py exec: scraped {len(results)} cases", file=sys.stderr)
    return 0


def cmd_study(args: argparse.Namespace) -> int:
    from .studies import StudyRunner, load_study_run, load_study_spec, study_spec_hash

    if args.study_command == "init":
        from .studies import write_citation_use_study

        path = write_citation_use_study(
            args.out,
            source=args.source,
            provider=args.provider,
            model=args.model,
            taxonomy=args.taxonomy,
            schema_path=args.schema,
        )
        _write_object({"created": str(path), "optional": True}, None)
        return 0
    if args.study_command == "validate":
        spec = load_study_spec(args.spec)
        _write_object(
            {
                "valid": True,
                "schema_version": spec.schema_version,
                "study_id": spec.id,
                "study_version": spec.version,
                "spec_sha256": study_spec_hash(spec),
            },
            None,
        )
        return 0
    if args.study_command == "plan":
        spec = load_study_spec(args.spec)
        plan = StudyRunner(spec, args.out).plan()
        Path(args.out).mkdir(parents=True, exist_ok=True)
        _write_object(plan, str(Path(args.out) / "plan.json"))
        return 0
    if args.study_command == "run":
        spec = load_study_spec(args.spec)
        run = StudyRunner(spec, args.out).run(
            resume=not args.no_resume,
            wait=args.wait,
            poll_interval=args.poll_interval,
            max_polls=args.max_polls,
        )
        _write_object(run.model_dump(mode="json"), None)
        return 0 if run.status in {"complete", "waiting"} else 1
    if args.study_command == "status":
        _write_object(load_study_run(args.run).model_dump(mode="json"), None)
        return 0
    if args.study_command == "resume":
        run_dir = Path(args.run).resolve()
        spec = load_study_spec(run_dir / "study.resolved.yaml")
        run = StudyRunner(spec, run_dir).run(
            resume=True,
            wait=args.wait,
            poll_interval=args.poll_interval,
            max_polls=args.max_polls,
        )
        _write_object(run.model_dump(mode="json"), None)
        return 0 if run.status in {"complete", "waiting"} else 1
    if args.study_command == "cancel":
        run_dir = Path(args.run).resolve()
        spec = load_study_spec(run_dir / "study.resolved.yaml")
        run = StudyRunner(spec, run_dir).cancel()
        _write_object(run.model_dump(mode="json"), None)
        return 0
    if args.study_command == "export":
        run_dir = Path(args.run).resolve()
        source = (
            run_dir / "records-latest.jsonl"
            if args.format == "jsonl"
            else run_dir / "records.parquet"
        )
        if not source.exists():
            print(f"echr-py study export: missing {source}", file=sys.stderr)
            return 1
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return 0
    if args.study_command == "benchmark-retrieval":
        from .retrieval import HybridRetriever, benchmark_retrieval

        retriever = HybridRetriever(
            args.database,
            mode=args.mode,
            embeddings=args.embeddings,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
        )
        _write_object(benchmark_retrieval(retriever, args.qrels), args.out)
        return 0
    print(
        "echr-py study: choose init, validate, plan, run, status, resume, cancel, export or benchmark-retrieval",
        file=sys.stderr,
    )
    return 2


def cmd_embeddings(args: argparse.Namespace) -> int:
    from .retrieval import build_embedding_index, verify_embedding_index

    if args.embeddings_command == "build":
        manifest = build_embedding_index(
            args.database,
            args.out,
            provider_name=args.provider,
            model=args.model,
            model_revision=args.model_revision,
            batch_size=args.batch_size,
            query_prompt=args.query_prompt,
            document_prompt=args.document_prompt,
            accelerator=args.accelerator,
            sections=args.sections,
            include_footnotes=args.include_footnotes,
        )
        _write_object(manifest.model_dump(mode="json"), None)
        return 0
    if args.embeddings_command == "verify":
        result = verify_embedding_index(args.input, database=args.database)
        _write_object(result, None)
        return 0 if result["valid"] else 1
    print("echr-py embeddings: choose build or verify", file=sys.stderr)
    return 2


def _read_items(path: str) -> list[dict]:
    """Load items from a .parquet or .jsonl file as a list of dicts."""
    from .utils.jsonl import iter_jsonl

    p = Path(path)
    if p.suffix == ".parquet":
        import pandas as pd

        frame = pd.read_parquet(p)
        # Pandas materialises nullable string/object cells as float NaN in
        # mixed Parquet frames; Pydantic correctly rejects those as strings.
        # Restore portable JSON-style nulls at the CLI boundary.
        frame = frame.astype(object).where(frame.notna(), None)
        return frame.to_dict("records")
    return list(iter_jsonl(p))


def cmd_citations_authority_import(args: argparse.Namespace) -> int:
    from .citations import import_authority_pdf

    authority = import_authority_pdf(
        args.pdf,
        args.out,
        updated_through=args.updated_through,
        language=args.language,
        source_url=args.source_url,
        retrieved_at=args.retrieved_at,
    )
    print(
        f"echr-py citations: imported {len(authority.entries)} exact references → "
        f"{args.out}/citation-authority.json + citation-authority.csv",
        file=sys.stderr,
    )
    return 0


def cmd_citations_authority_merge(args: argparse.Namespace) -> int:
    from .citations import load_authority, merge_authorities

    authority = merge_authorities([load_authority(path) for path in args.authority], args.out)
    print(
        f"echr-py citations: merged {len(authority.sources)} editions and "
        f"{len(authority.entries)} references → {args.out}",
        file=sys.stderr,
    )
    return 0


def cmd_citations_benchmark(args: argparse.Namespace) -> int:
    from .citations import (
        benchmark_citation_annotations,
        compare_citation_exports,
        fetch_benchmark,
        import_benchmark,
        load_benchmark_rows,
        load_competitor_citations,
        project_mumford_occurrences,
    )

    if args.benchmark_command == "fetch":
        manifest = fetch_benchmark(
            args.kind,
            args.out,
            revision=args.revision,
            expected_sha256=args.sha256,
            timeout=args.timeout,
            max_archive_bytes=args.max_bytes,
        )
        _write_object(manifest, None)
        return 0
    if args.benchmark_command == "import":
        report = import_benchmark(args.kind, args.source, args.out)
        _write_object(report, None)
        return 0
    if args.benchmark_command == "compare":
        local = load_benchmark_rows(args.local)
        if args.kind == "mumford":
            reference = load_benchmark_rows(args.reference)
            labels = load_benchmark_rows(args.labels) if args.labels else []
            projection_report = None
            if args.documents:
                local, projection_report = project_mumford_occurrences(
                    load_benchmark_rows(args.documents), local
                )
                if args.projected_out:
                    _write_records(local, args.projected_out)
            elif args.projected_out:
                print(
                    "echr-py citations benchmark: --projected-out requires --documents",
                    file=sys.stderr,
                )
                return 2
            report = benchmark_citation_annotations(
                reference,
                local,
                labels=labels,
                reference_scope=args.reference_scope,
            )
            if projection_report is not None:
                report["offset_projection"] = projection_report
        else:
            if args.documents or args.projected_out or args.reference_scope != "all":
                print(
                    "echr-py citations benchmark: Mumford projection options require "
                    "--kind mumford",
                    file=sys.stderr,
                )
                return 2
            report = compare_citation_exports(local, load_competitor_citations(args.reference))
        _write_object(report, args.out)
        return 0
    print("echr-py citations benchmark: choose 'fetch', 'import' or 'compare'", file=sys.stderr)
    return 2


def cmd_citations_catalog(args: argparse.Namespace) -> int:
    from .citations import (
        build_historical_catalog,
        load_authority,
        load_historical_catalog,
        verify_historical_catalog,
        write_historical_catalog,
    )
    from .models import Case

    if args.catalog_command == "build":
        cases = [Case.model_validate(row) for row in _read_items(args.input)] if args.input else []
        catalog = build_historical_catalog(cases, authority=load_authority(args.authority))
        path = write_historical_catalog(catalog, args.out)
        print(
            f"echr-py citations: {len(catalog.entries)} historical locators → {path}",
            file=sys.stderr,
        )
        return 0
    if args.catalog_command == "verify":
        catalog = load_historical_catalog(args.input)
        valid = verify_historical_catalog(catalog)
        print(
            f"echr-py citations: catalog checksum {'valid' if valid else 'INVALID'}",
            file=sys.stderr,
        )
        return 0 if valid else 1
    print("echr-py citations catalog: choose 'build' or 'verify'", file=sys.stderr)
    return 2


def cmd_citations_resolve(args: argparse.Namespace) -> int:
    from .citations import (
        load_authority,
        load_overrides,
        resolve_citations,
        write_resolution_artifacts,
    )
    from .models import Case

    sources = [Case.model_validate(row) for row in _read_items(args.input)]
    catalog = (
        [Case.model_validate(row) for row in _read_items(args.catalog)] if args.catalog else []
    )
    authority = load_authority(args.authority)
    overrides = load_overrides(args.overrides)
    cache = Path(args.out) / "lookup-cache.jsonl"

    if args.offline:
        result = _aio_run(
            resolve_citations(
                sources,
                authority=authority,
                catalog=catalog,
                overrides=overrides,
                cache_path=cache,
            )
        )
    else:
        from .main.client import AsyncHudocClient

        async def _online():
            async with AsyncHudocClient() as client:
                return await resolve_citations(
                    sources,
                    authority=authority,
                    catalog=catalog,
                    client=client,
                    overrides=overrides,
                    cache_path=cache,
                )

        result = _aio_run(_online())

    write_resolution_artifacts(result, args.out)
    print(
        f"echr-py citations: {result.report.resolved}/{result.report.mentions} resolved "
        f"({result.report.completeness:.1%}) → {args.out}",
        file=sys.stderr,
    )
    return 0 if result.report.complete else 1


def cmd_citations_review(args: argparse.Namespace) -> int:
    from .citations import write_review

    count, html_path, csv_path = write_review(args.resolution_dir, args.out, args.csv)
    print(
        f"echr-py citations: {count} references for review → {html_path}; {csv_path}",
        file=sys.stderr,
    )
    return 0


def cmd_citations_locate(args: argparse.Namespace) -> int:
    """Acquire/cache source HTML and emit deterministic occurrence artifacts."""
    import asyncio
    import hashlib
    import shutil
    from collections import Counter, defaultdict

    import aiohttp

    from . import config
    from .citations import (
        discover_citation_mentions,
        extract_citation_occurrences,
        load_historical_catalog,
        load_resolutions,
        resolve_citations,
        resolve_occurrence_paragraphs,
        write_occurrence_artifacts,
    )
    from .citations.models import (
        CitationOccurrenceReport,
        CitationOccurrenceResult,
    )
    from .main.client import AsyncHudocClient
    from .main.downloader import DOWNLOAD_HEADERS, fetch_document_html
    from .models import Case
    from .text import segment_html

    cases = [Case.model_validate(row) for row in _read_items(args.input)]
    resolution_groups: dict[str, list[Any]] = defaultdict(list)
    for resolution in load_resolutions(args.resolution_dir):
        if resolution.mention.source_itemid:
            resolution_groups[resolution.mention.source_itemid].append(resolution)

    out = Path(args.out)
    html_dir = Path(args.html_dir) if args.html_dir else out / "source-html"
    html_dir.mkdir(parents=True, exist_ok=True)

    async def acquire() -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
        available: dict[str, str] = {}
        manifest: list[dict[str, Any]] = []
        missing: list[str] = []
        semaphore = asyncio.Semaphore(config.HUDOC_CONCURRENCY)

        async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:

            async def one(case: Case) -> None:
                itemid = case.itemid
                if not itemid:
                    missing.append("<missing-itemid>")
                    return
                path = html_dir / f"{itemid}.html"
                source = "cache"
                html: str | None
                if path.exists():
                    html = path.read_text(encoding="utf-8")
                elif args.offline:
                    missing.append(itemid)
                    return
                else:
                    async with semaphore:
                        html = await fetch_document_html(session, itemid)
                    if html is None:
                        missing.append(itemid)
                        return
                    path.write_text(html, encoding="utf-8")
                    source = "hudoc"
                available[itemid] = html
                manifest.append(
                    {
                        "itemid": itemid,
                        "path": str(path),
                        "source": source,
                        "source_url": f"https://hudoc.echr.coe.int/?i={itemid}",
                        "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                    }
                )

            await asyncio.gather(*(one(case) for case in cases))
        return available, manifest, missing

    html_by_itemid, manifest, missing = _aio_run(acquire())
    manifest.sort(key=lambda value: str(value["itemid"]))
    (out / "source-html-manifest.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (out / "source-html-manifest.jsonl").write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in manifest),
        encoding="utf-8",
    )
    historical_catalog = load_historical_catalog()
    (out / "historical-catalog-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": historical_catalog.schema_version,
                "source_url": historical_catalog.source_url,
                "source_sha256": historical_catalog.source_sha256,
                "coverage_date": historical_catalog.coverage_date,
                "metadata_source_url": historical_catalog.metadata_source_url,
                "metadata_coverage_from": historical_catalog.metadata_coverage_from,
                "metadata_coverage_to": historical_catalog.metadata_coverage_to,
                "metadata_retrieved_at": historical_catalog.metadata_retrieved_at,
                "content_sha256": historical_catalog.content_sha256,
                "entry_count": len(historical_catalog.entries),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    spines = {}
    discovered_by_item: dict[str, list[Any]] = defaultdict(list)
    discovery_cache = out / "lookup-cache.jsonl"
    baseline_cache = Path(args.resolution_dir) / "lookup-cache.jsonl"
    if args.scope == "inclusive" and not discovery_cache.exists() and baseline_cache.is_file():
        shutil.copyfile(baseline_cache, discovery_cache)
    if args.scope == "inclusive":
        for case in cases:
            html = html_by_itemid.get(case.itemid or "")
            if html is None:
                continue
            sections = segment_html(
                html,
                doctype=case.doctype,
                doctype_branch=case.doctype_branch,
                document_id=case.itemid,
            )
            case.sections = sections
            if sections.spine is not None and case.itemid:
                spines[case.itemid] = sections.spine
                discovered_by_item[case.itemid] = discover_citation_mentions(
                    case, spine=sections.spine
                ).mentions

        async def resolve_discovered() -> list[Any]:
            mentions = [mention for values in discovered_by_item.values() for mention in values]
            if not mentions:
                return []
            if args.offline:
                return (
                    await resolve_citations(
                        cases,
                        mentions=mentions,
                        cache_path=discovery_cache,
                    )
                ).resolutions
            async with AsyncHudocClient() as client:
                return (
                    await resolve_citations(
                        cases,
                        mentions=mentions,
                        client=client,
                        cache_path=discovery_cache,
                    )
                ).resolutions

        for resolution in _aio_run(resolve_discovered()):
            if resolution.mention.source_itemid:
                resolution_groups[resolution.mention.source_itemid].append(resolution)

    occurrences = []
    diagnostics: list[dict[str, object]] = []
    methods: Counter[str] = Counter()
    scl_mentions = located_mentions = ambiguous_hits = unmatched_candidates = 0
    text_discovered_mentions = text_only_occurrences = scl_covered_occurrences = 0
    components: Counter[str] = Counter()
    section_counts: Counter[str] = Counter()
    inclusive_mentions = []
    inclusive_edges = []
    for case in cases:
        if not case.itemid or case.itemid not in html_by_itemid:
            diagnostics.append(
                {"code": "missing_html", "source_itemid": case.itemid or "<missing-itemid>"}
            )
            result = extract_citation_occurrences(
                case,
                resolution_groups.get(case.itemid or ""),
                html="",
                scope=args.scope,
            )
        else:
            result = extract_citation_occurrences(
                case,
                resolution_groups.get(case.itemid),
                html=html_by_itemid[case.itemid],
                spine=spines.get(case.itemid),
                scope=args.scope,
            )
        occurrences.extend(result.occurrences)
        diagnostics.extend(result.diagnostics)
        methods.update(result.report.methods)
        scl_mentions += result.report.scl_mentions
        located_mentions += result.report.located_mentions
        ambiguous_hits += result.report.ambiguous_hits
        unmatched_candidates += result.report.unmatched_candidates
        text_discovered_mentions += result.report.text_discovered_mentions
        text_only_occurrences += result.report.text_only_occurrences
        scl_covered_occurrences += result.report.scl_covered_occurrences
        components.update(result.report.components)
        section_counts.update(result.report.sections)
        inclusive_mentions.extend(result.mentions)
        inclusive_edges.extend(result.inclusive_edges)

    combined = CitationOccurrenceResult(
        occurrences=sorted(
            occurrences,
            key=lambda value: (
                value.source_itemid or "",
                value.document_start,
                value.document_end,
                value.mention_id,
            ),
        ),
        report=CitationOccurrenceReport(
            documents=len(cases),
            scl_mentions=scl_mentions,
            occurrences=len(occurrences),
            located_mentions=located_mentions,
            unlocated_mentions=max(0, scl_mentions - located_mentions),
            ambiguous_hits=ambiguous_hits,
            unmatched_candidates=unmatched_candidates,
            missing_html=len(missing),
            methods=dict(methods),
            text_discovered_mentions=text_discovered_mentions,
            text_only_occurrences=text_only_occurrences,
            scl_covered_occurrences=scl_covered_occurrences,
            components=dict(components),
            sections=dict(section_counts),
        ),
        diagnostics=diagnostics,
        mentions=inclusive_mentions,
        inclusive_edges=inclusive_edges,
    )
    target_missing: list[str] = []
    if args.resolve_paragraphs:
        target_dir = Path(args.target_html_dir) if args.target_html_dir else out / "target-html"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_ids = sorted(
            {
                value.target_itemid
                for value in combined.occurrences
                if value.target_itemid
                and value.resolution_scope == "document"
                and value.target_paragraphs
            }
        )

        async def acquire_targets() -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
            available: dict[str, str] = {}
            target_manifest: list[dict[str, Any]] = []
            unavailable: list[str] = []
            semaphore = asyncio.Semaphore(config.HUDOC_CONCURRENCY)
            async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:

                async def one(itemid: str) -> None:
                    path = target_dir / f"{itemid}.html"
                    source = "cache"
                    target_html: str | None
                    if path.exists():
                        target_html = path.read_text(encoding="utf-8")
                    elif itemid in html_by_itemid:
                        target_html = html_by_itemid[itemid]
                        path.write_text(target_html, encoding="utf-8")
                        source = "source-cache"
                    elif args.offline:
                        unavailable.append(itemid)
                        return
                    else:
                        async with semaphore:
                            target_html = await fetch_document_html(session, itemid)
                        if target_html is None:
                            unavailable.append(itemid)
                            return
                        path.write_text(target_html, encoding="utf-8")
                        source = "hudoc"
                    available[itemid] = target_html
                    target_manifest.append(
                        {
                            "itemid": itemid,
                            "path": str(path),
                            "source": source,
                            "source_url": f"https://hudoc.echr.coe.int/?i={itemid}",
                            "sha256": hashlib.sha256(target_html.encode("utf-8")).hexdigest(),
                        }
                    )

                await asyncio.gather(*(one(itemid) for itemid in target_ids))
            return available, target_manifest, unavailable

        target_html, target_manifest, target_missing = _aio_run(acquire_targets())
        target_manifest.sort(key=lambda value: str(value["itemid"]))
        (out / "target-html-manifest.jsonl").write_text(
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in target_manifest),
            encoding="utf-8",
        )
        target_spines = {
            itemid: segment_html(html, document_id=itemid).spine
            for itemid, html in target_html.items()
        }
        checksums = {str(value["itemid"]): str(value["sha256"]) for value in target_manifest}
        target_languages = {
            resolution.target.itemid: resolution.target.language
            for values in resolution_groups.values()
            for resolution in values
            if resolution.target is not None
            and resolution.target.itemid
            and resolution.target.language
        }
        combined = resolve_occurrence_paragraphs(
            combined,
            {key: spine for key, spine in target_spines.items() if spine is not None},
            target_languages=target_languages,
            target_checksums=checksums,
        )
        combined.report.target_html_missing = len(target_missing)
        combined.diagnostics.extend(
            {"code": "missing_target_html", "target_itemid": itemid} for itemid in target_missing
        )
    write_occurrence_artifacts(combined, out)
    print(
        f"echr-py citations: {len(occurrences)} occurrences in "
        f"{len(cases) - len(missing)}/{len(cases)} documents → {out}",
        file=sys.stderr,
    )
    return 1 if missing or target_missing else 0


def _load_citation_graph(args: argparse.Namespace):
    from .citations import CitationGraph
    from .models import Case

    if args.citations:
        return CitationGraph.from_artifacts(
            args.citations, require_complete=not args.allow_incomplete
        )
    cases = [Case.model_validate(row) for row in _read_items(args.input)]
    if args.use_extracted_appno:
        print(
            "echr-py: warning: --use-extracted-appno is deprecated and diagnostic only; "
            "body mentions are not target identifiers",
            file=sys.stderr,
        )
    graph = CitationGraph(cases, use_extracted_appno=args.use_extracted_appno)
    graph.resolve()
    return graph


def cmd_graph_metrics(args: argparse.Namespace) -> int:
    from .citations import IncompleteCitationResolutionError

    try:
        graph = _load_citation_graph(args)
    except IncompleteCitationResolutionError as exc:
        print(f"echr-py graph: refused measurement-grade output: {exc}", file=sys.stderr)
        return 2
    weight = None if args.weight == "binary" else "citation_count"
    df = graph.metrics_dataframe(allow_incomplete=args.allow_incomplete, weight=weight)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".csv":
        df.to_csv(out, index=False)
    else:
        df.to_parquet(out, index=False)
    metadata = {
        "measurement_grade": not args.allow_incomplete,
        "provisional": bool(args.allow_incomplete),
        "graph_scope": "one-hop cited targets",
        "weighting": args.weight,
        "resolution": graph.resolution_report,
    }
    out.with_suffix(out.suffix + ".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"echr-py graph: {len(df)} nodes → {out}", file=sys.stderr)
    return 0


def cmd_graph_html(args: argparse.Namespace) -> int:
    from .citations import IncompleteCitationResolutionError

    try:
        graph = _load_citation_graph(args)
        graph.to_html(args.out, max_nodes=args.max_nodes, allow_incomplete=args.allow_incomplete)
    except IncompleteCitationResolutionError as exc:
        print(f"echr-py graph: refused measurement-grade output: {exc}", file=sys.stderr)
        return 2
    print(f"echr-py graph: wrote {args.out}", file=sys.stderr)
    return 0


def cmd_graph_gexf(args: argparse.Namespace) -> int:
    from .citations import IncompleteCitationResolutionError

    try:
        graph = _load_citation_graph(args)
        weight = None if args.weight == "binary" else "citation_count"
        graph.to_gexf(
            args.out,
            with_metrics=not args.no_metrics,
            allow_incomplete=args.allow_incomplete,
            weight=weight,
        )
    except IncompleteCitationResolutionError as exc:
        print(f"echr-py graph: refused measurement-grade output: {exc}", file=sys.stderr)
        return 2
    print(f"echr-py graph: wrote {args.out}", file=sys.stderr)
    return 0


def cmd_graph_export(args: argparse.Namespace) -> int:
    from .graphs import (
        export_graph,
        from_citation_graph,
        from_networkx,
        from_paragraph_edges,
        load_graph_json,
    )

    if args.kind == "citation-paragraph":
        bundle = from_paragraph_edges(
            args.input,
            source_items=args.source_item,
            source_components=args.source_component,
            opinion_ids=args.opinion_id,
            footnotes_only=args.footnotes_only,
        )
    elif args.kind == "networkx":
        bundle = load_graph_json(args.input)
    elif args.kind == "citation-scl":
        from .citations import CitationGraph

        graph = CitationGraph.from_artifacts(args.input, require_complete=not args.allow_incomplete)
        bundle = from_citation_graph(graph, kind="citation-scl")
    else:
        import networkx as nx
        import pandas as pd

        root = Path(args.input)
        edges_path = root / "edges-inclusive.parquet" if root.is_dir() else root
        edges = pd.read_parquet(edges_path).to_dict("records")
        graph = nx.MultiDiGraph()
        for index, row in enumerate(edges):
            source = str(row.get("source") or row.get("source_node_id"))
            target = str(row.get("target") or row.get("target_node_id"))
            attrs = {k: v for k, v in row.items() if k not in {"source", "target"}}
            graph.add_edge(source, target, key=index, **attrs)
        bundle = from_networkx(graph, graph_id="citation-inclusive", kind="citation-inclusive")
    export_graph(bundle, args.out, fmt=args.format, max_nodes=args.max_nodes)
    print(f"echr-py graph: wrote {args.out}", file=sys.stderr)
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    from .bilingual import reconcile
    from .models import CaseCollection

    coll = CaseCollection.from_records(_read_items(args.input))
    policy: Literal["keep", "drop"] = "keep" if args.keep_extra_fre else "drop"
    result = reconcile(coll, extra_sibling_policy=policy)
    _write_records([c.model_dump(mode="json") for c in result.cases], args.out)
    if args.duplicates:
        _write_records([c.model_dump(mode="json") for c in result.duplicates], args.duplicates)
    if args.stats:
        _write_object(result.stats.model_dump(), args.stats)
    print(
        f"echr-py reconcile: {len(result.cases)} primaries, {len(result.duplicates)} duplicates",
        file=sys.stderr,
    )
    return 0


def cmd_rescue_french(args: argparse.Namespace) -> int:
    from . import config
    from .bilingual import rescue_french
    from .models import CaseCollection

    coll = CaseCollection.from_records(_read_items(args.input))
    stats = _aio_run(
        rescue_french(
            coll,
            checkpoint_path=args.checkpoint,
            resume=not args.no_resume,
            retry_errors=args.retry_errors,
            limit=args.limit,
            csv_export=args.csv,
            concurrency=args.concurrency or config.HUDOC_CONCURRENCY,
        )
    )
    if args.download_texts:
        from .main.downloader import AsyncDocumentDownloader

        fr_ids = [c.french_itemid for c in coll if c.french_itemid]
        dl = AsyncDocumentDownloader(args.download_texts)
        _aio_run(dl.download_batch(fr_ids))
    if args.out:
        _write_records([c.model_dump(mode="json") for c in coll], args.out)
    print(
        f"echr-py rescue-french: {stats.matched} matched, "
        f"{stats.no_sibling} no-sibling, {stats.errors} errors",
        file=sys.stderr,
    )
    return 0


def cmd_corpus_build(args: argparse.Namespace) -> int:
    from .bilingual import build_corpus

    report = _aio_run(
        build_corpus(
            args.out,
            limit=args.limit,
            page_size=args.page_size,
            with_texts=not args.no_texts,
            text_format=args.format,
            rescue=not args.no_rescue,
            save_docx=args.docx,
            resolve_case_citations=args.citations,
            citation_authority=args.citation_authority,
            citation_overrides=args.citation_overrides,
            extra_sibling_policy="keep" if args.keep_extra_fre else "drop",
            selection=args.selection,
            rich_sections=args.rich_sections,
            **_filters_from_args(args),
        )
    )
    print(
        f"echr-py corpus: {report.searched} searched → "
        f"{report.reconcile.eng_matched_fre} matched pairs; saved to {report.out_dir}",
        file=sys.stderr,
    )
    return 0


def cmd_corpus_validate(args: argparse.Namespace) -> int:
    from .bilingual import validate_corpus

    report = validate_corpus(args.input, out=args.out)
    print(
        f"echr-py corpus: checked {report.files_checked} files; "
        f"{'valid' if report.valid else 'invalid'} → {args.out}",
        file=sys.stderr,
    )
    return 0 if report.valid else 2


def cmd_corpus_package(args: argparse.Namespace) -> int:
    from .bilingual import package_corpus

    report = package_corpus(args.input, args.out)
    print(
        f"echr-py corpus: packaged {report.files} files → {report.archive} ({report.sha256})",
        file=sys.stderr,
    )
    return 0


def _aio_run(coro):
    import asyncio

    return asyncio.run(coro)


def cmd_local_search(args: argparse.Namespace) -> int:
    from .local import run_search

    print(
        run_search(
            args.data_dir,
            args.mode,
            args.query,
            limit=args.limit,
            fmt=args.format,
            date_from=args.date_from,
            date_to=args.date_to,
            respondent=args.respondent,
        )
    )
    return 0


def cmd_local_browse(args: argparse.Namespace) -> int:
    from .local import run_browse

    print(
        run_browse(
            args.data_dir,
            args.table,
            stats=args.stats,
            columns=args.columns,
            limit=args.limit,
            fmt=args.format,
        )
    )
    return 0


def cmd_local_export(args: argparse.Namespace) -> int:
    from .local import export_data

    tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
    results = export_data(args.data_dir, args.out, fmt=args.format, tables=tables)
    for name, (path, rows) in results.items():
        print(f"  {name}: {rows} rows → {path}", file=sys.stderr)
    print(f"echr-py local export: {len(results)} tables → {args.out}", file=sys.stderr)
    return 0


def cmd_local_index_paragraphs(args: argparse.Namespace) -> int:
    from .local import build_paragraph_index

    stats = build_paragraph_index(args.data_dir, args.database)
    _write_object({"database": args.database, **stats}, None)
    return 0


def cmd_local_paragraphs(args: argparse.Namespace) -> int:
    from .local import get_paragraphs

    if args.itemid:
        rows = get_paragraphs(
            args.database,
            args.itemid,
            para_id=args.para_id,
            section=args.section,
            limit=args.limit,
        )
    elif args.query:
        from .retrieval import HybridRetriever

        rows = HybridRetriever(
            args.database,
            mode=args.mode,
            embeddings=args.embeddings,
            top_k=args.limit,
            candidate_k=args.candidate_k,
            filters={
                "section": args.section,
                "language": args.language,
                "source_component": args.source_component,
                "opinion_id": args.opinion_id,
                "date_from": args.date_from,
                "date_to": args.date_to,
            },
        ).search(args.query)
    else:
        print("echr-py local paragraphs: provide query or --itemid", file=sys.stderr)
        return 2
    _write_records(rows, args.out)
    return 0


def cmd_codebook(args: argparse.Namespace) -> int:
    from .local import write_codebook

    out = write_codebook(args.out)
    print(f"echr-py codebook: written to {out}", file=sys.stderr)
    return 0


def cmd_keypoints(args: argparse.Namespace) -> int:
    from .thesaurus import search_keypoints

    matches = search_keypoints(args.query)
    if not matches:
        print(f"No ECHR keypoints match {args.query!r}", file=sys.stderr)
        return 1
    for tid, label in matches:
        print(f"{tid}\t{label}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "mcp":
        from .mcp import run

        if not args.enable_jobs:
            run()
            return 0
        required = {
            "--job-root": args.job_root,
            "--allow-input-root": args.allow_input_root,
            "--allow-provider": args.allow_provider,
            "--allow-study": args.allow_study,
            "--pricing-file": args.pricing_file,
            "--max-job-budget-usd": args.max_job_budget_usd,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            parser.error("--enable-jobs requires " + ", ".join(missing))
        if args.max_job_budget_usd <= 0:
            parser.error("--max-job-budget-usd must be positive")
        from .studies import StudyJobManager

        manager = StudyJobManager.from_pricing_file(
            output_root=args.job_root,
            input_roots=args.allow_input_root,
            allowed_providers=set(args.allow_provider),
            allowed_studies=set(args.allow_study),
            pricing_file=args.pricing_file,
            max_job_budget_usd=args.max_job_budget_usd,
            max_workers=args.job_workers,
        )
        run(job_manager=manager)
        return 0

    if args.command == "gui":
        from .gui import launch

        return launch()

    if args.command == "search":
        return cmd_search(args)
    if args.command == "count":
        return cmd_count(args)
    if args.command == "smart-fetch":
        return cmd_smart_fetch(args)
    if args.command == "fetch-case":
        return cmd_fetch_case(args)
    if args.command == "versions":
        return cmd_versions(args)
    if args.command == "dispositive":
        return cmd_dispositive(args)
    if args.command == "segment":
        return cmd_segment(args)
    if args.command == "exec":
        handlers = {
            "search": cmd_exec_search,
            "count": cmd_exec_count,
            "search-documents": cmd_exec_search_documents,
            "fetch-case": cmd_exec_fetch_case,
            "download-raw": cmd_exec_download_raw,
            "extract-text": cmd_exec_extract_text,
            "scrape-related": cmd_exec_scrape_related,
        }
        if not args.exec_command:
            print(f"echr-py exec: choose one of {sorted(handlers)}", file=sys.stderr)
            return 2
        return handlers[args.exec_command](args)
    if args.command == "study":
        return cmd_study(args)
    if args.command == "embeddings":
        return cmd_embeddings(args)
    if args.command == "citations":
        if args.citations_command == "authority":
            if args.authority_command == "import":
                return cmd_citations_authority_import(args)
            if args.authority_command == "merge":
                return cmd_citations_authority_merge(args)
            print("echr-py citations authority: choose 'import' or 'merge'", file=sys.stderr)
            return 2
        if args.citations_command == "benchmark":
            return cmd_citations_benchmark(args)
        if args.citations_command == "resolve":
            return cmd_citations_resolve(args)
        if args.citations_command == "catalog":
            return cmd_citations_catalog(args)
        if args.citations_command == "locate":
            return cmd_citations_locate(args)
        if args.citations_command == "review":
            return cmd_citations_review(args)
        print(
            "echr-py citations: choose 'authority', 'benchmark', 'resolve', 'locate' or 'review'",
            file=sys.stderr,
        )
        return 2
    if args.command == "graph":
        if args.graph_command == "metrics":
            return cmd_graph_metrics(args)
        if args.graph_command == "html":
            return cmd_graph_html(args)
        if args.graph_command == "gexf":
            return cmd_graph_gexf(args)
        if args.graph_command == "export":
            return cmd_graph_export(args)
        print("echr-py graph: choose 'metrics', 'html', 'gexf' or 'export'", file=sys.stderr)
        return 2
    if args.command == "reconcile":
        return cmd_reconcile(args)
    if args.command == "rescue-french":
        return cmd_rescue_french(args)
    if args.command == "corpus":
        if args.corpus_command == "build":
            return cmd_corpus_build(args)
        if args.corpus_command == "validate":
            return cmd_corpus_validate(args)
        if args.corpus_command == "package":
            return cmd_corpus_package(args)
        print("echr-py corpus: choose 'build', 'validate' or 'package'", file=sys.stderr)
        return 2
    if args.command == "local":
        if args.local_command == "search":
            return cmd_local_search(args)
        if args.local_command == "browse":
            return cmd_local_browse(args)
        if args.local_command == "export":
            return cmd_local_export(args)
        if args.local_command == "index-paragraphs":
            return cmd_local_index_paragraphs(args)
        if args.local_command == "paragraphs":
            return cmd_local_paragraphs(args)
        print(
            "echr-py local: choose 'search', 'browse', 'export', "
            "'index-paragraphs' or 'paragraphs'",
            file=sys.stderr,
        )
        return 2
    if args.command == "codebook":
        return cmd_codebook(args)
    if args.command == "keypoints":
        return cmd_keypoints(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
