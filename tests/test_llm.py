"""Tests for the optional LLM module.

We don't hit the Google API. Instead we exercise the cost math and verify
the import contract (lazy import with helpful error message when google-genai
is missing).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_estimate_cost_math():
    from hudoc_py import config
    from hudoc_py.llm import estimate_cost

    cost = estimate_cost(1_000_000, 2_000_000, 0)
    expected = config.GEMINI_PRICE_PER_M_INPUT + 2 * config.GEMINI_PRICE_PER_M_OUTPUT
    assert cost == pytest.approx(expected, rel=1e-6)


def test_estimate_cost_includes_thinking():
    from hudoc_py import config
    from hudoc_py.llm import estimate_cost

    cost = estimate_cost(0, 0, 1_000_000)
    assert cost == pytest.approx(config.GEMINI_PRICE_PER_M_THINKING, rel=1e-6)


def test_gemini_result_dataclass():
    from hudoc_py.llm import GeminiResult

    r = GeminiResult(data={"x": 1}, model="gemini-2.5-flash")
    assert r.ok is True
    assert r.data == {"x": 1}

    r_err = GeminiResult(data={}, model="m", error="boom")
    assert r_err.ok is False


def test_get_gemini_client_uses_resolved_key(monkeypatch, tmp_path):
    """Mock google-genai and assert the API key is sourced from config."""
    from hudoc_py import config, llm

    fake_client = MagicMock(name="GeminiClient")
    fake_module = MagicMock()
    fake_module.Client.return_value = fake_client

    monkeypatch.setattr(llm.client, "_import_genai", lambda: (fake_module, MagicMock()))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-42")
    monkeypatch.setattr(config, "_resources_dir", lambda: tmp_path)

    client = llm.get_gemini_client()

    fake_module.Client.assert_called_once_with(api_key="test-key-42")
    assert client is fake_client


def test_extract_structured_handles_429_then_succeeds(monkeypatch, tmp_path):
    """Simulate a rate-limit error on the first call followed by success."""
    from hudoc_py import config, llm
    from hudoc_py.llm import GeminiResult

    # Build a fake response object with text + usage_metadata.
    fake_response = MagicMock()
    fake_response.text = '{"answer": "ok"}'
    fake_response.usage_metadata = MagicMock(
        prompt_token_count=100,
        candidates_token_count=20,
        thoughts_token_count=0,
    )

    call_count = {"n": 0}

    def fake_generate(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return fake_response

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = fake_generate

    fake_types = MagicMock()
    monkeypatch.setattr(llm.client, "_import_genai", lambda: (MagicMock(), fake_types))
    monkeypatch.setattr(llm.client, "INITIAL_BACKOFF", 0.0)  # speed up the test
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "_resources_dir", lambda: tmp_path)

    result = llm.extract_structured(fake_client, "prompt", {})
    assert isinstance(result, GeminiResult)
    assert result.ok
    assert result.data == {"answer": "ok"}
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert call_count["n"] == 2
    config_kwargs = fake_types.GenerateContentConfig.call_args.kwargs
    assert config_kwargs["response_json_schema"] == {}
    assert "response_schema" not in config_kwargs
