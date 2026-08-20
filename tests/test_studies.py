"""Bounded study infrastructure and optional citation-use profiles."""

import json

import pytest

from hudoc_py.llm import ExtractResult
from hudoc_py.studies import (
    BatchStageManifest,
    StageSpec,
    StudyRunner,
    StudySpec,
    citation_taxonomy_schema,
    load_study_spec,
    unitize,
    write_citation_use_study,
)


class FakeProvider:
    name = "fake"

    def count_tokens(self, text, *, model=None):
        return max(1, len(text) // 4)

    def extract(self, prompt, response_schema, **kwargs):
        return ExtractResult(
            provider="fake",
            model=kwargs.get("model") or "fake-model",
            input_tokens=10,
            output_tokens=10,
            data={
                "data": {
                    "source_voice": "court",
                    "complaint_articles": ["8"],
                    "consideration": "positive",
                    "proposition": "The authority states the governing test.",
                    "confidence": 0.9,
                    "needs_review": False,
                },
                "evidence": [
                    {
                        "field_path": "consideration",
                        "paragraph_id": "p42",
                        "quote": "applied the principles in Example",
                    }
                ],
            },
        )


def _occurrence():
    return {
        "occurrence_id": "occ-1",
        "source_itemid": "001-1",
        "source_para_id": "p42",
        "source_section": "the_law",
        "source_component": "majority",
        "source_context": "The Court applied the principles in Example to this complaint.",
        "target_itemid": "001-2",
        "target_paragraphs": ["42-44"],
        "represented_by": "Ms Counsel",
    }


def test_citation_occurrences_have_unique_source_addresses():
    spec = StudySpec(
        id="test",
        version="1",
        source={"kind": "local", "path": "unused.jsonl"},
        unit="citation_occurrence",
        stages=[{"id": "select", "kind": "select"}],
    )
    first = _occurrence()
    second = {**first, "occurrence_id": "occ-2"}

    units = unitize(spec, [first, second])

    assert [unit["source_id"] for unit in units] == ["occ-1", "occ-2"]
    assert units[0]["text"] == first["source_context"]
    assert units[0]["target_paragraphs"] == ["42-44"]


def test_optional_citation_use_template_requires_explicit_model(tmp_path):
    source = tmp_path / "occurrences.jsonl"
    source.write_text(json.dumps(_occurrence()) + "\n", encoding="utf-8")
    study_path = write_citation_use_study(
        tmp_path / "study.yaml",
        source=source,
        provider="fake",
        model="fake-model",
        taxonomy="minimal",
    )

    spec = load_study_spec(study_path)

    assert spec.stages[0].provider == "fake"
    assert spec.stages[0].model == "fake-model"
    assert spec.metadata["optional"] is True
    assert citation_taxonomy_schema("minimal")["$id"] == "hudoc-citation-use-minimal/v1"


def test_study_verifies_quotes_and_retains_represented_by(tmp_path):
    source = tmp_path / "occurrences.jsonl"
    source.write_text(json.dumps(_occurrence()) + "\n", encoding="utf-8")
    schema = citation_taxonomy_schema("minimal")
    spec = StudySpec(
        id="citation-use-test",
        version="1",
        source={"kind": "local", "path": str(source)},
        unit="citation_occurrence",
        stages=[
            StageSpec(
                id="label",
                kind="extract",
                provider="fake",
                model="fake-model",
                prompt="{{text}}\n{{citation_json}}",
                response_schema=schema,
                required_evidence=True,
            )
        ],
    )
    runner = StudyRunner(
        spec,
        tmp_path / "run",
        provider_factory=lambda *args, **kwargs: FakeProvider(),
    )

    run = runner.run()

    assert run.status == "complete"
    record = json.loads((tmp_path / "run" / "records.jsonl").read_text().splitlines()[0])
    assert record["status"] == "ok"
    assert record["evidence"][0]["start"] == 10
    selection = json.loads(
        (tmp_path / "run" / "source-selection.jsonl").read_text().splitlines()[0]
    )
    assert selection["represented_by"] == "Ms Counsel"


def test_evidence_offsets_select_repeated_quote_and_missing_offsets_are_invalid(tmp_path):
    unit = {"source_id": "u", "itemid": "i", "text": "same and same"}
    from hudoc_py.studies import EvidenceRef, verify_evidence

    verified, errors = verify_evidence(
        [EvidenceRef(field_path="/claim", quote="same", start=9, end=13)], unit
    )
    assert not errors
    assert verified[0].start == 9

    verified, errors = verify_evidence([EvidenceRef(field_path="/claim", quote="same")], unit)
    assert not verified
    assert "ambiguous" in errors[0]


class CapturingProvider(FakeProvider):
    def __init__(self):
        self.calls = []

    def extract(self, prompt, response_schema, **kwargs):
        self.calls.append((prompt, kwargs))
        return super().extract(prompt, response_schema, **kwargs)


class ErrorProvider(FakeProvider):
    def extract(self, prompt, response_schema, **kwargs):
        return ExtractResult(provider="fake", model="fake", data={}, error="provider failed")


def test_study_resume_aggregates_and_no_resume_refuses_existing_output(tmp_path):
    source = tmp_path / "occurrences.jsonl"
    rows = [_occurrence(), {**_occurrence(), "occurrence_id": "occ-2"}]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    spec = StudySpec(
        id="resume-test",
        version="1",
        source={"kind": "local", "path": str(source)},
        unit="citation_occurrence",
        stages=[StageSpec(id="label", kind="extract", provider="fake", model="fake", prompt="{{text}}")],
    )
    provider = CapturingProvider()
    runner = StudyRunner(spec, tmp_path / "run", provider_factory=lambda *args, **kwargs: provider)

    first = runner.run()
    resumed = runner.run(resume=True)

    assert first.records == resumed.records == 2
    assert first.requests == resumed.requests == 2
    assert len(provider.calls) == 2
    with pytest.raises(FileExistsError):
        runner.run(resume=False)


def test_study_propagates_settings_and_stage_outputs(tmp_path):
    source = tmp_path / "occurrences.jsonl"
    source.write_text(json.dumps(_occurrence()) + "\n", encoding="utf-8")
    provider = CapturingProvider()
    spec = StudySpec(
        id="stages-test",
        version="1",
        source={"kind": "local", "path": str(source)},
        unit="citation_occurrence",
        stages=[
            StageSpec(
                id="first",
                kind="extract",
                provider="fake",
                model="fake",
                prompt="{{text}}",
                temperature=0.25,
                max_output_tokens=321,
            ),
            StageSpec(
                id="second",
                kind="extract",
                provider="fake",
                model="fake",
                prompt="prior={{previous_output_json}} all={{stage_outputs_json}}",
            ),
        ],
    )

    run = StudyRunner(
        spec, tmp_path / "run", provider_factory=lambda *args, **kwargs: provider
    ).run()

    assert run.status == "complete"
    assert provider.calls[0][1]["temperature"] == 0.25
    assert provider.calls[0][1]["max_output_tokens"] == 321
    assert '"consideration":"positive"' in provider.calls[1][0]


def test_all_provider_errors_still_export_a_valid_parquet_audit(tmp_path):
    import pandas as pd

    source = tmp_path / "documents.jsonl"
    source.write_text(json.dumps({"itemid": "one", "text": "Source"}) + "\n")
    spec = StudySpec(
        id="provider-error-export",
        version="1",
        source={"kind": "local", "path": str(source)},
        stages=[StageSpec(
            id="label",
            kind="extract",
            provider="fake",
            model="fake",
            prompt="{{text}}",
        )],
    )
    run = StudyRunner(
        spec, tmp_path / "run", provider_factory=lambda *args, **kwargs: ErrorProvider()
    ).run()
    exported = pd.read_parquet(tmp_path / "run" / "records.parquet")
    assert run.status == "partial"
    assert run.errors == 1
    assert exported.loc[0, "status"] == "error"
    assert exported.loc[0, "data"] is None


def test_evidence_fields_are_required_individually(tmp_path):
    source = tmp_path / "occurrences.jsonl"
    source.write_text(json.dumps(_occurrence()) + "\n", encoding="utf-8")
    spec = StudySpec(
        id="evidence-fields-test",
        version="1",
        source={"kind": "local", "path": str(source)},
        unit="citation_occurrence",
        stages=[StageSpec(
            id="label",
            kind="extract",
            provider="fake",
            model="fake",
            prompt="{{text}}",
            required_evidence=True,
            evidence_fields=["/proposition"],
            response_schema=citation_taxonomy_schema("minimal"),
        )],
    )

    run = StudyRunner(
        spec, tmp_path / "run", provider_factory=lambda *args, **kwargs: FakeProvider()
    ).run()

    assert run.status == "partial"
    record = json.loads((tmp_path / "run" / "records.jsonl").read_text().splitlines()[0])
    assert record["status"] == "invalid"
    assert "missing verified evidence for /proposition" in record["warnings"]


class FakeBatchAdapter:
    def __init__(self):
        self.rows = []
        self.cancelled = False

    def prepare(self, rows, path):
        self.rows = list(rows)
        path.write_text("\n".join(json.dumps(row) for row in self.rows) + "\n")
        return len(self.rows)

    def submit(self, *, path, stage_id, model, task_ids):
        import hashlib

        return BatchStageManifest(
            stage_id=stage_id,
            provider="gemini",
            model=model,
            job_id="jobs/1",
            state="submitted",
            task_ids=task_ids,
            requests_path=str(path),
            requests_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            submitted_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )

    def poll(self, manifest):
        return manifest.model_copy(update={"state": "succeeded"})

    def retrieve(self, manifest, output_path):
        output_path.write_text("{}\n", encoding="utf-8")
        results = {
            row["task_id"]: ExtractResult(
                provider="gemini",
                model=manifest.model,
                input_tokens=3,
                output_tokens=2,
                cost_usd=0.001,
                data={"label": "ok"},
            )
            for row in self.rows
        }
        return manifest.model_copy(
            update={"results_path": str(output_path), "results_sha256": "fixture"}
        ), results

    def cancel(self, manifest):
        self.cancelled = True
        return manifest.model_copy(update={"state": "cancelled"})


def test_native_batch_submits_waits_and_resumes(tmp_path):
    source = tmp_path / "occurrences.jsonl"
    source.write_text(json.dumps(_occurrence()) + "\n", encoding="utf-8")
    adapter = FakeBatchAdapter()
    provider = CapturingProvider()
    spec = StudySpec(
        id="batch-test",
        version="1",
        source={"kind": "local", "path": str(source)},
        unit="citation_occurrence",
        stages=[StageSpec(
            id="label",
            kind="extract",
            provider="gemini",
            model="gemini-2.5-flash",
            prompt="{{text}}",
            batch=True,
        )],
    )
    runner = StudyRunner(
        spec,
        tmp_path / "run",
        provider_factory=lambda *args, **kwargs: provider,
        batch_adapter_factory=lambda *args, **kwargs: adapter,
    )

    waiting = runner.run()
    complete = runner.run(resume=True)

    assert waiting.status == "waiting"
    assert waiting.waiting_stage == "label"
    assert complete.status == "complete"
    assert complete.requests == 1
    assert complete.cost_usd == 0.001
    assert json.loads((tmp_path / "run" / "records.jsonl").read_text())["status"] == "ok"


def test_unsupported_native_batch_is_rejected():
    with pytest.raises(ValueError, match="native batch"):
        StudySpec(
            id="bad-batch",
            version="1",
            source={"kind": "local", "path": "unused"},
            stages=[StageSpec(
                id="label",
                kind="extract",
                provider="anthropic",
                model="model",
                prompt="x",
                batch=True,
            )],
        )
