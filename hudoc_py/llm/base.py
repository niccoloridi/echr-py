"""Provider-agnostic LLM primitives: result type, provider protocol, pricing.

The concrete providers live in :mod:`hudoc_py.llm.providers`; the legacy
Gemini functions in :mod:`hudoc_py.llm.client` construct the same
:class:`ExtractResult` (aliased there as ``GeminiResult``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .. import config
from ..config import ModelPricing

logger = logging.getLogger(__name__)

__all__ = ["ExtractResult", "Provider", "ModelPricing", "estimate_cost_for"]


@dataclass
class ExtractResult:
    """Result of one structured-extraction LLM call, any provider."""

    data: dict[str, Any]
    model: str
    provider: str = "gemini"
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    elapsed_seconds: float = 0.0
    error: str | None = None
    raw_text: str | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.error is None

    def meta(self) -> dict[str, Any]:
        """Serializable ``_meta`` block for checkpoint records."""
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cost_usd": self.cost_usd,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "error": self.error,
        }


@runtime_checkable
class Provider(Protocol):
    """Anything that can run a structured-extraction call."""

    name: str

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
    ) -> ExtractResult: ...

    def count_tokens(self, text: str, *, model: str | None = None) -> int: ...


_warned_models: set[str] = set()


def estimate_cost_for(
    model: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
    *,
    batch: bool = False,
) -> float:
    """Estimate the USD cost of a call from token counts via the pricing table.

    Unknown models cost 0.0 in this provider-local estimate with a one-time
    warning. Callers such as the study runner may apply an explicit pricing
    profile independently.
    """
    pricing = config.MODEL_PRICING.get(model)
    if pricing is None:
        if model not in _warned_models:
            _warned_models.add(model)
            logger.warning(
                "No built-in provider pricing for model %r; provider-local cost is 0 "
                "(an explicit runner pricing profile may still apply)",
                model,
            )
        return 0.0
    cost = (
        (input_tokens / 1_000_000) * pricing.input_per_m
        + (output_tokens / 1_000_000) * pricing.output_per_m
        + (thinking_tokens / 1_000_000) * pricing.thinking_per_m
    )
    if batch:
        cost *= pricing.batch_discount
    return round(cost, 6)
