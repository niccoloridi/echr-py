"""Gemini client wrapper for structured extraction.

Generalized for reusable structured extraction. Key properties:

* No prompts shipped – callers supply prompt and response_schema.
* API key resolution goes through :func:`hudoc_py.config.get_gemini_api_key`
  (env → ``Resources/gemini_api_key.txt``).
* Returns a typed :class:`GeminiResult` instead of a dict-with-_meta.
* google-genai is imported lazily so the rest of the package works without
  the LLM extra.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from typing import Any

from .. import config
from .base import ExtractResult

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0  # seconds
GEMINI_TIMEOUT = 600.0  # 10 minutes per call


def _import_genai():
    """Lazy import for ``google-genai`` with a helpful error message."""
    try:
        from google import genai
        from google.genai import types

        return genai, types
    except ImportError as exc:
        raise ImportError(
            "google-genai is required for hudoc_py.llm. "
            'Install it with: pip install "echr-py[llm]"'
        ) from exc


# Backwards-compatible name: the Gemini-specific result type is now the
# provider-agnostic ExtractResult (whose ``provider`` field defaults to
# "gemini", so existing keyword construction keeps working).
GeminiResult = ExtractResult


def get_gemini_client() -> Any:
    """Initialize and return a Gemini client using the resolved API key."""
    genai, _ = _import_genai()
    key = config.get_gemini_api_key(required=True)
    return genai.Client(api_key=key)


def count_tokens(client: Any, text: str, *, model: str | None = None) -> int:
    """Count tokens for the given text under the chosen model."""
    model = model or config.GEMINI_MODEL
    try:
        response = client.models.count_tokens(model=model, contents=text)
        return int(response.total_tokens)
    except Exception as exc:
        logger.warning("Failed to count tokens: %s", exc)
        return 0


def estimate_cost(input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float:
    """Estimate the USD cost of a call from token counts."""
    cost = (
        (input_tokens / 1_000_000) * config.GEMINI_PRICE_PER_M_INPUT
        + (output_tokens / 1_000_000) * config.GEMINI_PRICE_PER_M_OUTPUT
        + (thinking_tokens / 1_000_000) * config.GEMINI_PRICE_PER_M_THINKING
    )
    return round(cost, 6)


def extract_structured(
    client: Any,
    prompt: str,
    response_schema: dict | type,
    *,
    model: str | None = None,
    thinking_budget: int = 0,
    temperature: float = 0.0,
    max_output_tokens: int = 2048,
    timeout_seconds: float = GEMINI_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> GeminiResult:
    """Send a prompt to Gemini and parse its JSON response into a dict.

    Retries with exponential backoff on 429 / RESOURCE_EXHAUSTED.
    ``response_schema`` may be a JSON Schema dict or a Pydantic model class
    (whatever google-genai accepts).
    """
    _, types = _import_genai()
    model = model or config.GEMINI_MODEL

    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            logger.info("Gemini call starting (%s, thinking=%d)", model, thinking_budget)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    client.models.generate_content,
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**{
                        "response_mime_type": "application/json",
                        (
                            "response_json_schema"
                            if isinstance(response_schema, dict)
                            else "response_schema"
                        ): response_schema,
                        "temperature": temperature,
                        "max_output_tokens": max_output_tokens,
                        "thinking_config": types.ThinkingConfig(
                            thinking_budget=thinking_budget
                        ),
                    }),
                )
                response = future.result(timeout=timeout_seconds)

            elapsed = time.time() - t0
            raw_text = response.text
            data = json.loads(raw_text)

            usage = getattr(response, "usage_metadata", None)
            input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
            output_tokens = (
                int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
            )
            thinking_tokens = (
                int(getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0
            )
            cost = estimate_cost(input_tokens, output_tokens, thinking_tokens)

            logger.info(
                "Gemini complete in %.1fs – %s in + %s out, $%.4f",
                elapsed, input_tokens, output_tokens, cost,
            )
            return GeminiResult(
                data=data,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_tokens=thinking_tokens,
                cost_usd=cost,
                elapsed_seconds=elapsed,
                raw_text=raw_text,
            )

        except concurrent.futures.TimeoutError:
            err = f"Gemini timed out after {timeout_seconds}s"
            logger.error(err)
            return GeminiResult(data={}, model=model, error=err)

        except Exception as exc:
            msg = str(exc)
            if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) and attempt < max_retries:
                wait = INITIAL_BACKOFF * (2**attempt)
                logger.warning(
                    "Rate limited (attempt %d/%d); waiting %.1fs",
                    attempt + 1, max_retries + 1, wait,
                )
                time.sleep(wait)
                continue
            logger.error("Gemini error: %s", exc)
            return GeminiResult(data={}, model=model, error=msg)

    return GeminiResult(data={}, model=model, error="exhausted retries")
