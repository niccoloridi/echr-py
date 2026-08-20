"""Native study-batch adapter contracts without network access."""

import json
import sys
import types
from types import SimpleNamespace

from hudoc_py.studies.batch import GeminiBatchAdapter, OpenAIBatchAdapter


def _request():
    return {
        "task_id": "task-1",
        "model": "test-model",
        "prompt": "Label this",
        "schema": {"type": "object", "properties": {"label": {"type": "string"}}},
        "temperature": 0.2,
        "max_output_tokens": 99,
        "thinking_budget": 0,
        "system_instruction": "JSON only",
    }


class GeminiFiles:
    def upload(self, **kwargs):
        self.upload_kwargs = kwargs
        return SimpleNamespace(name="files/input")

    def download(self, **kwargs):
        self.download_kwargs = kwargs
        return json.dumps({
            "key": "task-1",
            "response": {
                "candidates": [{"content": {"parts": [{"text": '{"label":"ok"}'}]}}],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2},
            },
        }).encode()


class GeminiBatches:
    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(name="batches/job-1")

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        return SimpleNamespace(
            state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
            dest=SimpleNamespace(file_name="files/output"),
            error=None,
        )

    def cancel(self, **kwargs):
        self.cancel_kwargs = kwargs


def test_gemini_batch_lifecycle(tmp_path, monkeypatch):
    fake_types = SimpleNamespace(UploadFileConfig=lambda **kwargs: kwargs)
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = fake_types
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    client = SimpleNamespace(files=GeminiFiles(), batches=GeminiBatches())
    adapter = GeminiBatchAdapter(client)
    requests = tmp_path / "requests.jsonl"

    assert adapter.prepare([_request()], requests) == 1
    prepared = json.loads(requests.read_text())
    assert prepared["request"]["generation_config"]["temperature"] == 0.2
    assert prepared["request"]["generation_config"]["maxOutputTokens"] == 99
    assert prepared["request"]["generation_config"]["responseJsonSchema"] == _request()[
        "schema"
    ]
    assert "response_schema" not in prepared["request"]["generation_config"]
    manifest = adapter.submit(
        path=requests, stage_id="label", model="test-model", task_ids=["task-1"]
    )
    manifest = adapter.poll(manifest)
    manifest, results = adapter.retrieve(manifest, tmp_path / "results.jsonl")

    assert manifest.state == "succeeded"
    assert manifest.results_sha256
    assert results["task-1"].data == {"label": "ok"}
    assert results["task-1"].input_tokens == 4
    assert adapter.cancel(manifest).state == "cancelled"


class OpenAIFiles:
    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(id="file-input")

    def content(self, file_id):
        self.content_id = file_id
        return SimpleNamespace(content=json.dumps({
            "custom_id": "task-1",
            "response": {
                "body": {
                    "choices": [{"message": {"content": '{"label":"ok"}'}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }
            },
        }).encode())


class OpenAIBatches:
    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(id="batch-1")

    def retrieve(self, job_id):
        self.retrieve_id = job_id
        return SimpleNamespace(status="completed", output_file_id="file-output", errors=None)

    def cancel(self, job_id):
        self.cancel_id = job_id


def test_openai_batch_lifecycle(tmp_path):
    client = SimpleNamespace(files=OpenAIFiles(), batches=OpenAIBatches())
    adapter = OpenAIBatchAdapter(client)
    requests = tmp_path / "requests.jsonl"

    assert adapter.prepare([_request()], requests) == 1
    prepared = json.loads(requests.read_text())
    assert prepared["body"]["response_format"]["type"] == "json_schema"
    manifest = adapter.submit(
        path=requests, stage_id="label", model="test-model", task_ids=["task-1"]
    )
    manifest = adapter.poll(manifest)
    manifest, results = adapter.retrieve(manifest, tmp_path / "results.jsonl")

    assert manifest.state == "succeeded"
    assert results["task-1"].data == {"label": "ok"}
    assert results["task-1"].output_tokens == 3
    assert adapter.cancel(manifest).state == "cancelled"
