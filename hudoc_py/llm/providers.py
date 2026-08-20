"""Concrete LLM providers behind the :class:`~hudoc_py.llm.base.Provider` protocol.

* :class:`GeminiProvider` – Google Gemini via ``google-genai`` (the ``llm``
  extra), delegating to the legacy :func:`hudoc_py.llm.client.extract_structured`
  path, with a defensive branch for the gemini-3.x Interactions API.
* :class:`OpenAICompatProvider` – any OpenAI-compatible endpoint (Ollama,
  vLLM, OpenRouter, LM Studio; the ``llm-openai`` extra), with schema
  instruction injection and JSON-repair retries.
* :class:`AnthropicProvider` – direct Anthropic Messages API using the same
  downstream schema-validation contract.

Select one with :func:`get_provider`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import config
from ..utils.retry import is_rate_limit_error
from .base import ExtractResult, Provider, estimate_cost_for
from .client import GEMINI_TIMEOUT, MAX_RETRIES, extract_structured, get_gemini_client

logger = logging.getLogger(__name__)

INITIAL_BACKOFF = 2.0  # seconds; monkeypatched to 0 in tests


def _schema_to_dict(response_schema: dict | type) -> dict:
    """Accept a JSON-schema dict or a Pydantic model class."""
    if isinstance(response_schema, dict):
        return response_schema
    if hasattr(response_schema, "model_json_schema"):
        return response_schema.model_json_schema()
    raise TypeError(
        f"response_schema must be a dict or Pydantic model class, "
        f"not {type(response_schema).__name__}"
    )


def _uses_interactions_api(model: str) -> bool:
    return model.startswith("gemini-3")


class GeminiProvider:
    """Google Gemini structured extraction."""

    name = "gemini"

    def __init__(self, client: Any = None, *, model: str | None = None):
        self._client = client
        self.model = model

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = get_gemini_client()
        return self._client

    def extract(
        self,
        prompt: str,
        response_schema: dict | type,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
        thinking_budget: int = 0,
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
        timeout_seconds: float = GEMINI_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> ExtractResult:
        model = model or self.model or config.GEMINI_MODEL
        client = self._get_client()

        if _uses_interactions_api(model):
            result = self._try_interactions(
                client,
                prompt,
                response_schema=_schema_to_dict(response_schema),
                system_instruction=system_instruction,
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            if result is not None:
                return result
            # fall through to generate_content

        full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
        return extract_structured(
            client,
            full_prompt,
            response_schema,
            model=model,
            thinking_budget=thinking_budget,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    def _try_interactions(
        self,
        client: Any,
        prompt: str,
        *,
        response_schema: dict[str, Any],
        system_instruction: str | None,
        model: str,
        temperature: float,
        max_output_tokens: int,
    ) -> ExtractResult | None:
        """gemini-3.x Interactions API path.

        The response-format type lives on a private google-genai import path
        that has shifted between releases, so any failure here (import,
        attribute, transport) falls back to ``generate_content`` by
        returning ``None``. This surface currently enforces the JSON mime
        type rather than a native JSON Schema, so the exact schema is also
        supplied as a system instruction and callers validate downstream.
        """
        try:
            from google.genai._gaos.types.interactions import TextResponseFormat

            t0 = time.time()
            response = client.interactions.create(
                model=model,
                input=prompt,
                system_instruction="\n\n".join(
                    part
                    for part in (
                        system_instruction,
                        _build_schema_instruction(response_schema),
                    )
                    if part
                ),
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                },
                response_format=TextResponseFormat(mime_type="application/json"),
            )
            raw_text = response.output_text or "{}"
            data = json.loads(raw_text)
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "total_input_tokens", 0) or 0) if usage else 0
            output_tokens = int(getattr(usage, "total_output_tokens", 0) or 0) if usage else 0
            return ExtractResult(
                data=data if isinstance(data, dict) else {},
                model=model,
                provider=self.name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost_for(model, input_tokens, output_tokens),
                elapsed_seconds=time.time() - t0,
                raw_text=raw_text,
            )
        except Exception as exc:
            logger.warning(
                "Interactions API unavailable for %s (%s); falling back to generate_content",
                model, exc,
            )
            return None

    def count_tokens(self, text: str, *, model: str | None = None) -> int:
        from .client import count_tokens

        return count_tokens(self._get_client(), text, model=model or self.model)


def _build_schema_instruction(schema: dict) -> str:
    """Render a JSON schema as a plain-text system instruction.

    Flat schemas get a readable field list; anything nested (objects, arrays,
    ``$defs``) is embedded as the full schema JSON, which local models follow
    well enough and never mis-renders.
    """
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    def _is_flat(spec: dict) -> bool:
        return spec.get("type") not in ("object", "array") and "$ref" not in spec

    lines = ["You MUST respond with a single JSON object matching this schema:"]
    if "$defs" in schema or not props or not all(_is_flat(s) for s in props.values()):
        lines.append(json.dumps(schema, indent=2))
    else:
        lines.append("{")
        for key, spec in props.items():
            req = " (REQUIRED)" if key in required else ""
            if "enum" in spec:
                enum_str = ", ".join(f'"{v}"' for v in spec["enum"])
                lines.append(f'  "{key}": one of [{enum_str}]{req}')
            elif spec.get("type") in ("number", "integer"):
                lines.append(f'  "{key}": <number>{req}')
            elif spec.get("type") == "boolean":
                lines.append(f'  "{key}": <true|false>{req}')
            else:
                desc = spec.get("description", "string")
                lines.append(f'  "{key}": <string: {desc}>{req}')
        lines.append("}")
    lines.append("")
    lines.append(
        "Return ONLY the JSON object. No markdown, no code fences, "
        "no explanation outside the JSON."
    )
    return "\n".join(lines)


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])
    return "\n".join(lines[1:])


class OpenAICompatProvider:
    """Any OpenAI-compatible chat-completions endpoint."""

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        client: Any = None,
    ):
        self.base_url = base_url or config.OPENAI_API_BASE
        self.api_key = api_key
        self.model = model
        self._client = client
        # Some servers (older Ollama, some vLLM configs) reject
        # response_format; after the first rejection we stop sending it.
        self._response_format_supported = True

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ImportError(
                    "openai is required for OpenAICompatProvider. "
                    'Install it with: pip install "echr-py[llm-openai]"'
                ) from exc
            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key or config.get_openai_api_key() or "not-needed",
            )
        return self._client

    def extract(
        self,
        prompt: str,
        response_schema: dict | type,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
        thinking_budget: int = 0,  # accepted for protocol parity; unused
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
        timeout_seconds: float = GEMINI_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> ExtractResult:
        del thinking_budget
        model = model or self.model or config.OPENAI_MODEL
        client = self._get_client()
        schema = _schema_to_dict(response_schema)
        required_keys = set(schema.get("required", []))

        system_content = _build_schema_instruction(schema)
        if system_instruction:
            system_content = f"{system_instruction}\n\n{system_content}"
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]

        t0 = time.time()
        last_error = "exhausted retries"
        for attempt in range(max_retries + 1):
            try:
                kwargs: dict[str, Any] = dict(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_output_tokens,
                    timeout=timeout_seconds,
                )
                if self._response_format_supported:
                    kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**kwargs)

                raw_text = _strip_code_fences(response.choices[0].message.content or "")
                data = json.loads(raw_text)
                if not isinstance(data, dict):
                    raise json.JSONDecodeError("not a JSON object", raw_text, 0)

                missing = required_keys - set(data.keys())
                if missing and attempt < max_retries:
                    logger.warning(
                        "Missing keys %s (attempt %d/%d); retrying",
                        missing, attempt + 1, max_retries + 1,
                    )
                    last_error = f"missing required keys: {sorted(missing)}"
                    continue

                usage = getattr(response, "usage", None)
                input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
                output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
                return ExtractResult(
                    data=data,
                    model=model,
                    provider=self.name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=estimate_cost_for(model, input_tokens, output_tokens),
                    elapsed_seconds=time.time() - t0,
                    raw_text=raw_text,
                )

            except json.JSONDecodeError as exc:
                last_error = f"JSON parse failure: {exc}"
                if attempt < max_retries:
                    logger.warning("Malformed JSON (attempt %d): %s", attempt + 1, exc)
                    continue

            except Exception as exc:
                msg = str(exc)
                if self._response_format_supported and "response_format" in msg:
                    logger.info("Endpoint rejects response_format; retrying without it")
                    self._response_format_supported = False
                    continue
                if is_rate_limit_error(exc) and attempt < max_retries:
                    wait = INITIAL_BACKOFF * (2**attempt)
                    logger.warning(
                        "Rate limited (attempt %d/%d); waiting %.1fs",
                        attempt + 1, max_retries + 1, wait,
                    )
                    time.sleep(wait)
                    continue
                logger.error("OpenAI-compatible API error: %s", exc)
                last_error = msg
                break

        return ExtractResult(data={}, model=model, provider=self.name, error=last_error)

    def count_tokens(self, text: str, *, model: str | None = None) -> int:
        """Best-effort heuristic (~4 chars per token); endpoints vary."""
        del model
        return len(text) // 4


class AnthropicProvider:
    """Direct Anthropic structured-generation adapter."""

    name = "anthropic"

    def __init__(self, *, model: str | None = None, api_key: str | None = None, client: Any = None):
        self.model = model
        if client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise ImportError(
                    "anthropic is required for AnthropicProvider. "
                    'Install it with: pip install "echr-py[llm-anthropic]"'
                ) from exc
            client = Anthropic(api_key=api_key)
        self._client = client

    def extract(
        self,
        prompt: str,
        response_schema: dict | type,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
        thinking_budget: int = 0,
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
        timeout_seconds: float = 600.0,
        max_retries: int = 3,
    ) -> ExtractResult:
        del thinking_budget, timeout_seconds
        model = model or self.model
        if not model:
            raise ValueError("AnthropicProvider requires an explicit model")
        schema = _schema_to_dict(response_schema)
        system = "\n\n".join(
            value for value in (system_instruction or "", _build_schema_instruction(schema)) if value
        )
        last_error = "unknown error"
        started = time.time()
        for attempt in range(max_retries + 1):
            try:
                response = self._client.messages.create(
                    model=model,
                    max_tokens=max_output_tokens,
                    temperature=temperature,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = "".join(
                    str(getattr(block, "text", "")) for block in getattr(response, "content", [])
                )
                data = json.loads(_strip_code_fences(raw))
                usage = getattr(response, "usage", None)
                input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                return ExtractResult(
                    data=data if isinstance(data, dict) else {},
                    model=model,
                    provider=self.name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=estimate_cost_for(model, input_tokens, output_tokens),
                    elapsed_seconds=time.time() - started,
                    raw_text=raw,
                )
            except Exception as exc:
                last_error = str(exc)
                if is_rate_limit_error(exc) and attempt < max_retries:
                    time.sleep(INITIAL_BACKOFF * (2**attempt))
                    continue
                break
        return ExtractResult(
            data={}, model=model, provider=self.name, error=last_error,
            elapsed_seconds=time.time() - started,
        )

    def count_tokens(self, text: str, *, model: str | None = None) -> int:
        try:
            value = self._client.messages.count_tokens(
                model=model or self.model,
                messages=[{"role": "user", "content": text}],
            )
            return int(value.input_tokens)
        except Exception:
            return len(text) // 4


def provider_capabilities(name: str) -> dict[str, bool]:
    resolved = name.lower()
    return {
        "structured_generation": resolved in {
            "gemini", "openai", "openai-compat", "ollama", "anthropic"
        },
        "embeddings": resolved in {"gemini", "openai", "openai-compat", "ollama"},
        "native_batch": resolved in {"gemini", "openai"},
        "token_counting": True,
    }


def get_provider(name: str | None = None, **kwargs: Any) -> Provider:
    """Resolve a provider: explicit ``name`` → ``HUDOC_LLM_PROVIDER`` env → gemini."""
    resolved = (name or config.DEFAULT_LLM_PROVIDER or "gemini").lower()
    if resolved == "gemini":
        return GeminiProvider(**kwargs)
    if resolved == "openai":
        kwargs.setdefault("base_url", "https://api.openai.com/v1")
        return OpenAICompatProvider(**kwargs)
    if resolved in ("openai-compat", "ollama"):
        return OpenAICompatProvider(**kwargs)
    if resolved == "anthropic":
        return AnthropicProvider(**kwargs)
    raise ValueError(
        f"Unknown LLM provider {resolved!r}: use 'gemini', 'openai' or 'anthropic'"
    )
