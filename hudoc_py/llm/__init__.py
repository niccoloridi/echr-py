"""Optional LLM integration.

Install with::

    pip install echr-py[llm]           # Gemini
    pip install echr-py[llm-openai]    # any OpenAI-compatible endpoint

This layer ships no domain prompts – it is generic plumbing for providers,
structured extraction, token counting and cost accounting. Study prompts and
schemas are supplied through :mod:`hudoc_py.studies` or application packages.
"""

from .base import ExtractResult, ModelPricing, Provider, estimate_cost_for
from .client import (
    GeminiResult,
    count_tokens,
    estimate_cost,
    extract_structured,
    get_gemini_client,
)
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAICompatProvider,
    get_provider,
    provider_capabilities,
)

__all__ = [
    "get_gemini_client",
    "extract_structured",
    "count_tokens",
    "estimate_cost",
    "estimate_cost_for",
    "GeminiResult",
    "ExtractResult",
    "ModelPricing",
    "Provider",
    "GeminiProvider",
    "OpenAICompatProvider",
    "AnthropicProvider",
    "get_provider",
    "provider_capabilities",
]
