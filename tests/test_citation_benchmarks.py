from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from hudoc_py.citations import (
    align_benchmark_annotations,
    benchmark_citation_annotations,
    compare_citation_exports,
    import_mumford,
    load_competitor_citations,
    parse_mumford_xmi,
    project_mumford_occurrences,
)
from hudoc_py.citations.benchmarks import fetch_benchmark
from hudoc_py.cli import main as cli_main


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, *, content_length: int | None = None):
        super().__init__(payload)
        self.headers = {
            "Content-Length": str(content_length if content_length is not None else len(payload))
        }


def _benchmark_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("source/records.json", "[]")
    return buffer.getvalue()


def test_fetch_benchmark_enforces_timeout_size_and_checksum(tmp_path):
    payload = _benchmark_zip()
    calls = []

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout))
        return _Response(payload)

    manifest = fetch_benchmark(
        "mumford",
        tmp_path,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        timeout=12.5,
        max_archive_bytes=len(payload),
        opener=opener,
    )
    assert calls[0][1] == 12.5
    assert manifest["archive_checksum_verified"] is True
    assert manifest["archive_bytes"] == len(payload)


def test_fetch_benchmark_rejects_oversize_and_bad_checksum(tmp_path):
    payload = _benchmark_zip()

    def opener(_request, *, timeout):
        assert timeout > 0
        return _Response(payload)

    with pytest.raises(ValueError, match="limit"):
        fetch_benchmark(
            "mumford",
            tmp_path / "large",
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            max_archive_bytes=len(payload) - 1,
            opener=opener,
        )
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch_benchmark(
            "mumford",
            tmp_path / "checksum",
            expected_sha256="0" * 64,
            opener=opener,
        )


def test_competitor_jsonl_import_and_exact_scope_comparison(tmp_path):
    path = tmp_path / "ecthr-pcr.jsonl"
    path.write_text(
        json.dumps({
            "citing_itemid": "001-source",
            "cited_itemid": "001-target",
            "citing_appno": "1/01",
            "cited_appno": "2/02",
            "paragraph_id": "42",
        }) + "\n",
        encoding="utf-8",
    )

    competitor = load_competitor_citations(path)
    report = compare_citation_exports(
        [{
            "source_itemid": "001-source",
            "target_itemid": "001-target",
            "source_appno": "1/01",
            "target_appno": "2/02",
        }],
        competitor,
    )

    assert competitor[0]["paragraph"] == "42"
    assert report["document_edges"]["shared"] == 1
    assert report["application_edges"]["shared"] == 1


def _mumford_xmi(path: Path, consideration: str = "Applied") -> None:
    text = "The Court followed Alpha v. State, no. 12/34, § 9."
    start = text.index("Alpha")
    end = text.index(", §")
    path.write_text(
        f'''<?xml version="1.0" encoding="UTF-8"?>
<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:cas="http:///uima/cas.ecore"
 xmlns:custom="http:///custom.ecore">
  <cas:Sofa xmi:id="1" sofaString="{text}" />
  <custom:Span xmi:id="2" begin="{start}" end="{end}"
    Citation="Alpha v. State, no. 12/34" label="citation"
    JudicialConsideration="{consideration}">
    <ArticleorProtocol>Article 6</ArticleorProtocol>
  </custom:Span>
</xmi:XMI>''',
        encoding="utf-8",
    )


def test_parse_and_import_mumford_xmi_preserves_offsets_and_annotators(tmp_path):
    folder = tmp_path / "Curation" / "000_allowed_6_JUDGMENT_001-123456.txt"
    folder.mkdir(parents=True)
    curated = folder / "CURATION_USER.xmi"
    _mumford_xmi(curated)
    raw_folder = tmp_path / "Raw_Annotations" / folder.name
    raw_folder.mkdir(parents=True)
    _mumford_xmi(raw_folder / "ANNOTATOR_A.xmi", "Distinguished")

    parsed = parse_mumford_xmi(curated)
    annotation = parsed["annotations"][0]
    assert annotation["source_itemid"] == "001-123456"
    assert annotation["exact_span"] == "Alpha v. State, no. 12/34"
    assert annotation["citation_appnos"] == ["12/34"]
    assert annotation["articles_or_protocols"] == ["Article 6"]
    assert annotation["curated"] is True

    report = import_mumford(tmp_path, tmp_path / "out")
    assert report["annotations"] == 2
    assert report["curated_annotations"] == 1
    assert report["individual_annotations"] == 1
    imported_document = json.loads(
        (tmp_path / "out" / "documents.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert not Path(imported_document["source_file"]).is_absolute()
    assert imported_document["source_file"].startswith("Curation/")


def test_mumford_appno_is_read_from_exact_span_when_attribute_has_name_only(tmp_path):
    folder = tmp_path / "Curation" / "000_allowed_6_JUDGMENT_001-123456.txt"
    folder.mkdir(parents=True)
    path = folder / "CURATION_USER.xmi"
    text = "See Alpha v. State, no. 12/34, § 9."
    start = text.index("Alpha")
    end = text.index(", §")
    path.write_text(
        f'''<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:cas="http:///cas.ecore"
 xmlns:custom="http:///custom.ecore"><cas:Sofa sofaString="{text}" />
 <custom:Span begin="{start}" end="{end}" Citation="Alpha v. State"
 JudicialConsideration="Applied" /></xmi:XMI>''',
        encoding="utf-8",
    )
    annotation = parse_mumford_xmi(path)["annotations"][0]
    assert annotation["citation_appnos"] == ["12/34"]


def test_benchmark_alignment_is_conservative_and_scores_optional_labels():
    annotations = [{
        "annotation_id": "a1",
        "source_itemid": "001-123456",
        "citation": "Alpha v. State, no. 12/34",
        "exact_span": "Alpha v. State, no. 12/34",
        "citation_appnos": ["12/34"],
        "judicial_consideration": "Applied",
        "curated": True,
    }]
    local = [{
        "occurrence_id": "o1",
        "source_itemid": "001-123456",
        "raw_span": "Alpha v. State, no. 12/34",
        "target_appnos": "12/34",
        "target_itemid": "001-654321",
        "target_paragraphs": ["9"],
        "source_component": "majority",
    }]

    aligned = align_benchmark_annotations(annotations, local)
    assert aligned[0]["occurrence_id"] == "o1"
    assert aligned[0]["alignment_status"] == "exact_span"

    report = benchmark_citation_annotations(
        annotations,
        local,
        labels=[{"occurrence_id": "o1", "data": {"consideration": "Applied"}}],
    )
    assert report["annotated_reference_recovery"] == 1.0
    assert report["exact_document_resolved"] == 1
    assert report["paragraph_pinpoint_resolved"] == 1
    assert report["treatment_macro_f1"] == 1.0
    assert report["treatment_krippendorff_alpha"] == 1.0


def test_repeated_equal_candidates_remain_ambiguous():
    annotation = {
        "source_itemid": "001-123456",
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State",
    }
    candidates = [
        {"occurrence_id": value, "source_itemid": "001-123456", "raw_span": "Alpha v. State"}
        for value in ("o1", "o2")
    ]
    row = align_benchmark_annotations([annotation], candidates)[0]
    assert row["alignment_status"] == "ambiguous"
    assert row["candidate_occurrence_ids"] == ["o1", "o2"]


def test_one_occurrence_cannot_recover_multiple_reference_annotations():
    annotations = [
        {
            "annotation_id": annotation_id,
            "source_itemid": "001-123456",
            "citation": "Alpha v. State",
            "exact_span": "Alpha v. State",
        }
        for annotation_id in ("a1", "a2")
    ]
    occurrence = {
        "occurrence_id": "o1",
        "source_itemid": "001-123456",
        "raw_span": "Alpha v. State",
    }
    aligned = align_benchmark_annotations(annotations, [occurrence])
    assert [row["alignment_status"] for row in aligned] == ["ambiguous", "ambiguous"]
    report = benchmark_citation_annotations(annotations, [occurrence])
    assert report["aligned"] == 0
    assert report["ambiguous"] == 2


def test_benchmark_report_separates_span_identity_and_resolution_metrics():
    annotations = [
        {
            "annotation_id": "a1",
            "source_itemid": "001-source",
            "start": 10,
            "end": 20,
            "exact_span": "Alpha case",
            "citation": "Alpha case",
            "citation_appnos": ["12/34"],
            "target_itemid": "001-target",
            "target_paragraphs": ["9"],
        },
        {
            "annotation_id": "a2",
            "source_itemid": "001-source",
            "exact_span": "Beta case",
            "citation": "Beta case",
        },
    ]
    local = [
        {
            "occurrence_id": "o1",
            "source_itemid": "001-source",
            "document_start": 10,
            "document_end": 20,
            "raw_span": "Alpha case",
            "target_appnos": ["12/34"],
            "target_itemid": "001-target",
            "target_paragraphs": ["9"],
            "target_paragraph_status": "resolved",
            "resolution_scope": "document",
        },
        {
            "occurrence_id": "o2",
            "source_itemid": "001-source",
            "raw_span": "Beta case",
            "resolution_scope": "unresolved",
        },
    ]
    report = benchmark_citation_annotations(annotations, local)
    assert report["schema_version"] == "hudoc-citation-benchmark-report/v2"
    assert report["occurrence_alignment"]["strict_source_span"] == 1
    assert report["occurrence_alignment"]["normalized_identity_context"] == 1
    assert report["exact_document_resolution"]["verified_accuracy"] == 1.0
    assert report["application_identification"]["verified_accuracy"] == 1.0
    assert report["pinpoint_recovery"]["exact_label_recall"] == 1.0
    assert report["target_paragraph_resolution"]["complete_mapping_rate"] == 1.0
    assert report["resolution_abstentions"] == 1


def test_alignment_accepts_native_occurrence_raw_text_field():
    annotation = {
        "source_itemid": "001-123456",
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State, no. 12/34",
    }
    candidate = {
        "occurrence_id": "o1",
        "source_itemid": "001-123456",
        "raw_text": "Alpha v. State, no. 12/34",
    }
    row = align_benchmark_annotations([annotation], [candidate])[0]
    assert row["alignment_status"] == "exact_span"
    assert row["occurrence_id"] == "o1"


def test_exact_source_offsets_disambiguate_repeated_printed_citations():
    annotation = {
        "source_itemid": "001-123456",
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State",
        "start": 80,
        "end": 94,
    }
    candidates = [
        {
            "occurrence_id": occurrence_id,
            "source_itemid": "001-123456",
            "raw_text": "Alpha v. State",
            "document_start": start,
            "document_end": start + 14,
        }
        for occurrence_id, start in (("o1", 20), ("o2", 80))
    ]
    row = align_benchmark_annotations([annotation], candidates)[0]
    assert row["alignment_status"] == "source_offsets"
    assert row["occurrence_id"] == "o2"


def test_source_overlap_disambiguates_envelope_from_shorter_annotation():
    annotation = {
        "source_itemid": "001-123456",
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State",
        "start": 80,
        "end": 94,
    }
    candidates = [
        {
            "occurrence_id": occurrence_id,
            "source_itemid": "001-123456",
            "raw_text": "Alpha v. State, no. 12/34",
            "document_start": start,
            "document_end": start + 27,
        }
        for occurrence_id, start in (("o1", 20), ("o2", 80))
    ]
    row = align_benchmark_annotations([annotation], candidates)[0]
    assert row["alignment_status"] == "source_overlap"
    assert row["occurrence_id"] == "o2"


def test_exact_span_containment_beats_same_paragraph_context():
    annotation = {
        "source_itemid": "001-123456",
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State, no. 12/34",
    }
    context = "See Alpha v. State, no. 12/34 and Beta v. State, no. 56/78."
    candidates = [
        {
            "occurrence_id": "alpha",
            "source_itemid": "001-123456",
            "raw_text": "Alpha v. State, no. 12/34 and ",
            "source_context": context,
        },
        {
            "occurrence_id": "beta",
            "source_itemid": "001-123456",
            "raw_text": "Beta v. State, no. 56/78.",
            "source_context": context,
        },
    ]
    row = align_benchmark_annotations([annotation], candidates)[0]
    assert row["alignment_status"] == "span_containment"
    assert row["occurrence_id"] == "alpha"


def test_appno_corroborates_name_context_in_multi_citation_paragraph():
    annotation = {
        "source_itemid": "001-123456",
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State (cited above)",
        "citation_appnos": ["12/34"],
    }
    context = (
        "See Alpha v. State (cited above), no. 12/34 and "
        "Beta v. State, no. 56/78."
    )
    candidates = [
        {
            "occurrence_id": "alpha",
            "source_itemid": "001-123456",
            "raw_text": "Alpha v. State, no. 12/34 and ",
            "source_context": context,
            "target_appnos": ["12/34"],
        },
        {
            "occurrence_id": "beta",
            "source_itemid": "001-123456",
            "raw_text": "Beta v. State, no. 56/78.",
            "source_context": context,
            "target_appnos": ["56/78"],
        },
    ]
    row = align_benchmark_annotations([annotation], candidates)[0]
    assert row["occurrence_id"] == "alpha"
    assert row["alignment_methods"] == ["normalized_citation", "target_appno"]


def test_mumford_projection_maps_full_context_into_xmi_offsets():
    sofa = "I. THE ISSUE\r\r\n42.\u00a0 See Alpha v. State, no. 12/34, § 9."
    context = "42. See Alpha v. State, no. 12/34, § 9."
    raw = "Alpha v. State, no. 12/34"
    block_start = context.index("Alpha")
    block_end = block_start + len(raw)
    original_start = 1234 + block_start
    documents = [{
        "source_itemid": "001-source",
        "source_text": sofa,
        "source_text_sha256": hashlib.sha256(sofa.encode()).hexdigest(),
        "curated": True,
    }]
    occurrences = [{
        "occurrence_id": "o1",
        "source_itemid": "001-source",
        "source_component": "majority",
        "source_section": "the_law",
        "source_context": context,
        "block_start": block_start,
        "block_end": block_end,
        "document_start": original_start,
        "document_end": original_start + len(raw),
        "raw_text": raw,
    }]

    projected, report = project_mumford_occurrences(documents, occurrences)

    assert report["projected_occurrences"] == 1
    assert projected[0]["document_start"] == sofa.index("Alpha")
    assert projected[0]["document_end"] == sofa.index("Alpha") + len(raw)
    assert projected[0]["benchmark_projected_text"] == raw
    assert projected[0]["benchmark_projection_reversible"] is True
    assert projected[0]["hudoc_document_start"] == original_start


def test_mumford_projection_abstains_on_opinions_and_ambiguous_contexts():
    context = "42. See Alpha v. State."
    sofa = f"{context}\r\r\n{context}"
    base = {
        "source_itemid": "001-source",
        "source_context": context,
        "block_start": context.index("Alpha"),
        "block_end": context.index("Alpha") + len("Alpha v. State"),
        "raw_text": "Alpha v. State",
    }
    documents = [{"source_itemid": "001-source", "source_text": sofa, "curated": True}]
    occurrences = [
        {**base, "occurrence_id": "majority", "source_component": "majority"},
        {
            **base,
            "occurrence_id": "opinion",
            "source_component": "opinion",
            "source_opinion_id": "op-1",
        },
    ]

    projected, report = project_mumford_occurrences(documents, occurrences)

    assert projected == []
    assert report["statuses"] == {
        "ambiguous_normalized_context": 1,
        "outside_reference_component": 1,
    }


def test_source_overlap_requires_compatible_citation_identity():
    checksum = hashlib.sha256(b"source").hexdigest()
    annotation = {
        "source_itemid": "001-source",
        "source_text_sha256": checksum,
        "start": 10,
        "end": 24,
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State",
    }
    wrong = {
        "occurrence_id": "wrong",
        "source_itemid": "001-source",
        "benchmark_source_text_sha256": checksum,
        "document_start": 10,
        "document_end": 23,
        "raw_text": "Beta v. State",
        "source_context": "Alpha v. State and Beta v. State",
    }

    row = align_benchmark_annotations([annotation], [wrong])[0]

    assert row["alignment_status"] == "unmatched"


def test_broad_reference_span_cannot_substitute_for_annotated_identity():
    checksum = hashlib.sha256(b"source").hexdigest()
    annotation = {
        "source_itemid": "001-source",
        "source_text_sha256": checksum,
        "start": 10,
        "end": 80,
        "citation": "Alpha v. State",
        "exact_span": "discussion; Beta v. State, no. 56/78; more discussion",
        "citation_appnos": ["12/34"],
    }
    wrong = {
        "occurrence_id": "wrong",
        "source_itemid": "001-source",
        "benchmark_source_text_sha256": checksum,
        "document_start": 20,
        "document_end": 44,
        "raw_text": "Beta v. State, no. 56/78",
        "target_appnos": ["56/78"],
    }

    row = align_benchmark_annotations([annotation], [wrong])[0]

    assert row["alignment_status"] == "unmatched"


def test_xmi_hash_mismatch_disables_offset_evidence():
    annotation = {
        "source_itemid": "001-source",
        "source_text_sha256": "a" * 64,
        "start": 10,
        "end": 24,
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State",
    }
    candidate = {
        "occurrence_id": "o1",
        "source_itemid": "001-source",
        "benchmark_source_text_sha256": "b" * 64,
        "document_start": 10,
        "document_end": 24,
        "raw_text": "Alpha v. State",
    }

    row = align_benchmark_annotations([annotation], [candidate])[0]

    assert row["occurrence_id"] == "o1"
    assert row["alignment_family"] == "normalized_identity_context"
    assert "source_offsets" not in row["alignment_methods"]


def test_benchmark_decodes_nested_parquet_style_json_columns():
    annotations = [{
        "source_itemid": "001-source",
        "citation": "Alpha v. State",
        "exact_span": "Alpha v. State, no. 12/34",
        "citation_appnos": ["12/34"],
    }]
    local = [{
        "occurrence_id": "o1",
        "source_itemid": "001-source",
        "raw_text": "Alpha v. State, no. 12/34",
        "target_itemid": "001-target",
        "target_appnos": '["12/34"]',
        "target_paragraphs": '["9"]',
        "target_paragraph_resolutions": '[{"status": "exact"}]',
        "paragraph_resolution_status": "resolved",
        "resolution_scope": "document",
    }]

    report = benchmark_citation_annotations(annotations, local)

    assert report["application_identification"]["verified_accuracy"] == 1.0
    assert report["target_paragraph_resolution"]["complete_mapping_rate"] == 1.0


def test_published_mumford_audit_matches_code_and_documented_counts():
    root = Path(__file__).resolve().parents[1]
    audit_path = root / "docs" / "benchmarks" / "mumford-full-inclusive-audit.json"
    if not audit_path.is_file():
        pytest.skip("published benchmark documentation is not in this source archive")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    software = audit["software"]
    for field, relative in {
        "benchmarks_py_sha256": "hudoc_py/citations/benchmarks.py",
        "occurrences_py_sha256": "hudoc_py/citations/occurrences.py",
        "resolver_py_sha256": "hudoc_py/citations/resolver.py",
        "reporter_py_sha256": "hudoc_py/citations/reporter.py",
        "paragraphs_py_sha256": "hudoc_py/citations/paragraphs.py",
        "segmentation_py_sha256": "hudoc_py/text/segmentation.py",
        "spine_py_sha256": "hudoc_py/text/spine.py",
        "common_models_py_sha256": "hudoc_py/models/common.py",
        "citation_models_py_sha256": "hudoc_py/citations/models.py",
        "comparison_example_sha256": "examples/benchmark_mumford_inclusive.py",
    }.items():
        assert software[field] == hashlib.sha256((root / relative).read_bytes()).hexdigest()

    recovery = audit["selected_annotation_recovery"]
    claim = (root / "docs" / "claim-audit.md").read_text(encoding="utf-8")
    methodology = (root / "docs" / "mumford-methodology-audit.md").read_text(
        encoding="utf-8"
    )
    documented = (
        f"{recovery['aligned_one_to_one']:,}/{recovery['denominator']:,}"
    )
    assert documented in claim
    assert (
        f"{recovery['aligned_one_to_one']:,}\nof the "
        f"{recovery['denominator']:,}"
    ) in methodology


def test_cli_projects_full_mumford_occurrences_before_comparison(tmp_path):
    sofa = "42.\u00a0 See Alpha v. State, no. 12/34."
    raw = "Alpha v. State, no. 12/34"
    checksum = hashlib.sha256(sofa.encode()).hexdigest()
    documents = tmp_path / "documents.jsonl"
    reference = tmp_path / "annotations.jsonl"
    local = tmp_path / "occurrences.jsonl"
    output = tmp_path / "comparison.json"
    projected = tmp_path / "projected.jsonl"
    documents.write_text(json.dumps({
        "source_itemid": "001-source",
        "source_text": sofa,
        "source_text_sha256": checksum,
        "curated": True,
    }) + "\n", encoding="utf-8")
    start = sofa.index("Alpha")
    reference.write_text(json.dumps({
        "source_itemid": "001-source",
        "source_text_sha256": checksum,
        "start": start,
        "end": start + len(raw),
        "citation": "Alpha v. State",
        "exact_span": raw,
        "source_label": "ECHR case law",
        "curated": True,
    }) + "\n", encoding="utf-8")
    context = "42. See Alpha v. State, no. 12/34."
    block_start = context.index("Alpha")
    local.write_text(json.dumps({
        "occurrence_id": "o1",
        "source_itemid": "001-source",
        "source_component": "majority",
        "source_section": "the_law",
        "source_context": context,
        "block_start": block_start,
        "block_end": block_start + len(raw),
        "document_start": 999,
        "document_end": 999 + len(raw),
        "raw_text": raw,
    }) + "\n", encoding="utf-8")

    result = cli_main([
        "citations", "benchmark", "compare",
        "--kind", "mumford",
        "--reference", str(reference),
        "--local", str(local),
        "--documents", str(documents),
        "--reference-scope", "echr",
        "--projected-out", str(projected),
        "--out", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == 0
    assert report["aligned"] == 1
    assert report["occurrence_alignment"]["strict_source_span"] == 1
    assert report["offset_projection"]["projected_occurrences"] == 1
    assert json.loads(projected.read_text(encoding="utf-8"))["document_start"] == start


def test_mumford_illegal_xml_reference_is_repaired_without_offset_drift(tmp_path):
    folder = tmp_path / "000_allowed_6_JUDGMENT_001-123456.txt"
    folder.mkdir()
    path = folder / "CURATION_USER.xmi"
    path.write_text(
        '''<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:cas="http:///cas.ecore"
 xmlns:custom="http:///custom.ecore"><cas:Sofa sofaString="Al&#30;Skeini" />
 <custom:Span begin="0" end="9" Citation="Al&#30;Skeini"
 JudicialConsideration="Neutral" /></xmi:XMI>''',
        encoding="utf-8",
    )
    parsed = parse_mumford_xmi(path)
    assert parsed["document"]["source_text"] == "Al�Skeini"
    assert parsed["document"]["xml_illegal_character_replacements"] == 2
    assert parsed["annotations"][0]["exact_span"] == "Al�Skeini"


def test_ecthr_pcr_application_mapping_zip_expands_citation_edges(tmp_path):
    path = tmp_path / "ecthr-pcr.json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "dataset.json",
            json.dumps({"1/01": {"date": "2020-01-01", "citations": ["2/02", "3/03B"]}}),
        )
    rows = load_competitor_citations(path)
    assert [(row["source_appno"], row["target_appno"]) for row in rows] == [
        ("1/01", "2/02"),
        ("1/01", "3/03B"),
    ]
