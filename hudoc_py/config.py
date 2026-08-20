"""Centralized configuration: endpoints, rate limits, API key resolution.

API key resolution order (Gemini):
    1. ``GEMINI_API_KEY`` environment variable
    2. ``Resources/gemini_api_key.txt`` relative to the repository root
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --- Endpoints --------------------------------------------------------------

# HUDOC main
HUDOC_BASE = "https://hudoc.echr.coe.int"
HUDOC_SEARCH_URL = f"{HUDOC_BASE}/app/query/results"
HUDOC_HTML_URL = f"{HUDOC_BASE}/app/conversion/docx/html/body"
# Raw DOCX of a document: {HUDOC_DOCX_URL}?library=ECHR&id={itemid}
HUDOC_DOCX_URL = f"{HUDOC_BASE}/app/conversion/docx"
HUDOC_RANKING_MODEL_ID = "11111111-0000-0000-0000-000000000000"

# HUDOC-EXEC
HUDOC_EXEC_BASE = "https://hudoc.exec.coe.int"
HUDOC_EXEC_SEARCH_URL = f"{HUDOC_EXEC_BASE}/app/query/results"
HUDOC_EXEC_HTML_URL = f"{HUDOC_EXEC_BASE}/app/conversion/docx/html/body"
# Raw DOCX of an execution document: {HUDOC_EXEC_DOCX_URL}?library=EXEC&id={content_store_id}
HUDOC_EXEC_DOCX_URL = f"{HUDOC_EXEC_BASE}/app/conversion/docx"
HUDOC_EXEC_PDF_URL = f"{HUDOC_EXEC_BASE}/app/conversion/pdf"
HUDOC_EXEC_RANKING_MODEL_ID = "44444444-b0a6-44c9-bb6b-5886b928f985"

# --- Rate limits & concurrency ---------------------------------------------

HUDOC_RATE_LIMIT_SECONDS = 0.5
HUDOC_MAX_RETRIES = 3
HUDOC_CONCURRENCY = 20
HUDOC_MAX_HTML_BYTES = 32 * 1024 * 1024
HUDOC_MAX_BINARY_BYTES = 256 * 1024 * 1024

# --- Caching ----------------------------------------------------------------

_legacy_data_dir = Path.home() / ".hudoc-py" / "data"
_default_data_dir = (
    _legacy_data_dir if _legacy_data_dir.exists() else Path.home() / ".echr-py" / "data"
)
DATA_DIR = Path(
    os.environ.get("ECHR_PY_DATA_DIR")
    or os.environ.get("HUDOC_PY_DATA_DIR")
    or _default_data_dir
)

# --- LLM --------------------------------------------------------------------

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BATCH_MODEL = "gemini-2.5-flash"
GEMINI_MAX_WORKERS = 5
GEMINI_SUBMIT_DELAY = 0.05

# Gemini Flash pricing (USD per million tokens). Update as Google revises.
# Kept as flat constants for backwards compatibility; they seed MODEL_PRICING.
GEMINI_PRICE_PER_M_INPUT = 0.30
GEMINI_PRICE_PER_M_OUTPUT = 2.50
GEMINI_PRICE_PER_M_THINKING = 3.50


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens for one model."""

    input_per_m: float
    output_per_m: float
    thinking_per_m: float = 0.0
    batch_discount: float = 0.5  # Gemini Batch API charges ~50% of realtime


MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-2.5-flash": ModelPricing(
        input_per_m=GEMINI_PRICE_PER_M_INPUT,
        output_per_m=GEMINI_PRICE_PER_M_OUTPUT,
        thinking_per_m=GEMINI_PRICE_PER_M_THINKING,
    ),
}

# Provider selection: explicit argument > HUDOC_LLM_PROVIDER env > "gemini".
DEFAULT_LLM_PROVIDER = os.environ.get("HUDOC_LLM_PROVIDER", "gemini")

# OpenAI-compatible endpoint (Ollama / vLLM / OpenRouter / LM Studio).
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "http://localhost:11434/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "llama3.1")
MISTRAL_OCR_MODEL = os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest")


# --- API key resolution -----------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resources_dir() -> Path:
    return _REPO_ROOT / "Resources"


def get_gemini_api_key(required: bool = False) -> str | None:
    """Resolve the Gemini API key.

    Order: env ``GEMINI_API_KEY`` → ``Resources/gemini_api_key.txt``.

    If ``required`` is True and no key is found, raises ``RuntimeError``.
    """
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env.strip()

    path = _resources_dir() / "gemini_api_key.txt"
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key

    if required:
        raise RuntimeError(
            "GEMINI_API_KEY not set and Resources/gemini_api_key.txt not found. "
            "Set the environment variable or place the key in the Resources/ folder."
        )
    return None


def get_openai_api_key(required: bool = False) -> str | None:
    """Resolve the key for an OpenAI-compatible endpoint.

    Order: env ``OPENAI_API_KEY`` → ``Resources/openai_api_key.txt``. Local
    servers (Ollama, LM Studio) usually accept any non-empty string.
    """
    env = os.environ.get("OPENAI_API_KEY")
    if env:
        return env.strip()

    path = _resources_dir() / "openai_api_key.txt"
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key

    if required:
        raise RuntimeError(
            "OPENAI_API_KEY not set and Resources/openai_api_key.txt not found. "
            "Set the environment variable or place the key in the Resources/ folder."
        )
    return None


def get_mistral_api_key(required: bool = False) -> str | None:
    """Resolve ``MISTRAL_API_KEY`` with the same local-development fallback."""
    env = os.environ.get("MISTRAL_API_KEY")
    if env:
        return env.strip()
    path = _resources_dir() / "mistral_api_key.txt"
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    if required:
        raise RuntimeError("MISTRAL_API_KEY not set and Resources/mistral_api_key.txt not found.")
    return None
