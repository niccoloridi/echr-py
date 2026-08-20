"""Tests for the provider abstraction: compat alias, pricing, OpenAI-compat provider."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from hudoc_py import config
from hudoc_py.llm import ExtractResult, GeminiResult, estimate_cost_for, get_provider
from hudoc_py.llm.providers import (
    GeminiProvider,
    OpenAICompatProvider,
    _build_schema_instruction,
)

FLAT_SCHEMA = {
    "type": "object",
    "properties": {
        "polarity": {"type": "string", "enum": ["positive", "negative"]},
        "confidence": {"type": "number"},
    },
    "required": ["polarity"],
}


def test_gemini_result_is_extract_result():
    assert GeminiResult is ExtractResult
    r = GeminiResult(data={"x": 1}, model="gemini-2.5-flash")
    assert r.provider == "gemini"
    assert r.ok


def test_extract_result_meta_shape():
    r = ExtractResult(data={}, model="m", provider="openai", input_tokens=10, cost_usd=0.1)
    meta = r.meta()
    assert meta["provider"] == "openai"
    assert meta["model"] == "m"
    assert meta["input_tokens"] == 10
    assert meta["error"] is None


def test_estimate_cost_for_known_model_matches_legacy():
    from hudoc_py.llm import estimate_cost

    legacy = estimate_cost(1_000_000, 500_000, 100_000)
    table = estimate_cost_for("gemini-2.5-flash", 1_000_000, 500_000, 100_000)
    assert table == pytest.approx(legacy)


def test_estimate_cost_for_batch_discount():
    full = estimate_cost_for("gemini-2.5-flash", 1_000_000, 0)
    batch = estimate_cost_for("gemini-2.5-flash", 1_000_000, 0, batch=True)
    assert batch == pytest.approx(full * 0.5)


def test_estimate_cost_for_unknown_model_is_zero():
    assert estimate_cost_for("mystery-model-9000", 1_000_000, 1_000_000) == 0.0


def test_get_provider_resolution(monkeypatch):
    assert isinstance(get_provider("gemini"), GeminiProvider)
    assert isinstance(get_provider("openai"), OpenAICompatProvider)
    monkeypatch.setattr(config, "DEFAULT_LLM_PROVIDER", "openai")
    assert isinstance(get_provider(), OpenAICompatProvider)
    monkeypatch.setattr(config, "DEFAULT_LLM_PROVIDER", "gemini")
    assert isinstance(get_provider(), GeminiProvider)
    with pytest.raises(ValueError):
        get_provider("claude")


def test_schema_instruction_flat_lists_fields():
    text = _build_schema_instruction(FLAT_SCHEMA)
    assert '"polarity": one of ["positive", "negative"] (REQUIRED)' in text
    assert '"confidence": <number>' in text
    assert "code fences" in text


def test_schema_instruction_nested_embeds_json():
    nested = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    }
    text = _build_schema_instruction(nested)
    assert '"type": "array"' in text  # full schema embedded


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = reply
        response.usage = MagicMock(prompt_tokens=100, completion_tokens=20)
        return response


def _provider_with(replies) -> tuple[OpenAICompatProvider, FakeCompletions]:
    completions = FakeCompletions(replies)
    client = MagicMock()
    client.chat.completions = completions
    return OpenAICompatProvider(client=client, model="test-model"), completions


def test_openai_provider_happy_path():
    provider, completions = _provider_with(['{"polarity": "positive"}'])
    result = provider.extract("classify this", FLAT_SCHEMA)
    assert result.ok
    assert result.data == {"polarity": "positive"}
    assert result.provider == "openai"
    assert result.input_tokens == 100
    assert completions.calls[0]["response_format"] == {"type": "json_object"}


def test_openai_provider_strips_code_fences():
    provider, _ = _provider_with(['```json\n{"polarity": "negative"}\n```'])
    result = provider.extract("x", FLAT_SCHEMA)
    assert result.data == {"polarity": "negative"}


def test_openai_provider_retries_malformed_json():
    provider, completions = _provider_with(["not json{{{", '{"polarity": "positive"}'])
    result = provider.extract("x", FLAT_SCHEMA)
    assert result.ok
    assert len(completions.calls) == 2


def test_openai_provider_retries_missing_required_key():
    provider, completions = _provider_with(['{"confidence": 0.4}', '{"polarity": "positive"}'])
    result = provider.extract("x", FLAT_SCHEMA)
    assert result.ok
    assert result.data["polarity"] == "positive"
    assert len(completions.calls) == 2


def test_openai_provider_malformed_exhaustion_returns_error():
    provider, _ = _provider_with(["bad"] * 10)
    result = provider.extract("x", FLAT_SCHEMA, max_retries=2)
    assert not result.ok
    assert result.error is not None and "JSON parse failure" in result.error


def test_openai_provider_rate_limit_backoff(monkeypatch):
    from hudoc_py.llm import providers

    monkeypatch.setattr(providers, "INITIAL_BACKOFF", 0.0)
    provider, completions = _provider_with(
        [RuntimeError("429 Too Many Requests"), '{"polarity": "positive"}']
    )
    result = provider.extract("x", FLAT_SCHEMA)
    assert result.ok
    assert len(completions.calls) == 2


def test_openai_provider_response_format_fallback():
    provider, completions = _provider_with(
        [RuntimeError("this endpoint does not support response_format"),
         '{"polarity": "positive"}']
    )
    result = provider.extract("x", FLAT_SCHEMA)
    assert result.ok
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]


def test_openai_provider_accepts_pydantic_schema():
    from pydantic import BaseModel

    class Out(BaseModel):
        polarity: str

    provider, _ = _provider_with(['{"polarity": "positive"}'])
    result = provider.extract("x", Out)
    assert result.ok


def test_openai_provider_lazy_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    provider = OpenAICompatProvider(model="m")
    with pytest.raises(ImportError, match="llm-openai"):
        provider._get_client()


def test_gemini_provider_delegates_to_extract_structured(monkeypatch):
    from hudoc_py.llm import providers

    captured = {}

    def fake_extract(client, prompt, schema, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return ExtractResult(data={"ok": True}, model=kwargs.get("model") or "m")

    monkeypatch.setattr(providers, "extract_structured", fake_extract)
    provider = GeminiProvider(client=MagicMock(), model="gemini-2.5-flash")
    result = provider.extract("user text", {}, system_instruction="SYSTEM RULES")
    assert result.ok
    assert captured["prompt"].startswith("SYSTEM RULES\n\n")
    assert captured["kwargs"]["model"] == "gemini-2.5-flash"


def test_gemini_interactions_receives_exact_schema_instruction(monkeypatch):
    interactions = pytest.importorskip("google.genai._gaos.types.interactions")

    class FakeResponse:
        output_text = '{"polarity": "positive"}'
        usage = None

    client = MagicMock()
    client.interactions.create.return_value = FakeResponse()
    monkeypatch.setattr(interactions, "TextResponseFormat", MagicMock())

    provider = GeminiProvider(client=client, model="gemini-3.1-flash-lite")
    result = provider.extract(
        "classify this",
        FLAT_SCHEMA,
        system_instruction="SYSTEM RULES",
    )

    assert result.ok
    instruction = client.interactions.create.call_args.kwargs["system_instruction"]
    assert instruction.startswith("SYSTEM RULES\n\n")
    assert '"polarity": one of ["positive", "negative"] (REQUIRED)' in instruction


def test_count_tokens_heuristic():
    provider = OpenAICompatProvider(client=MagicMock(), model="m")
    assert provider.count_tokens("x" * 400) == 100
