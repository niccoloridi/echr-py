"""Lazy document-to-text conversion for raw HUDOC-EXEC files."""

from __future__ import annotations

import base64
import io
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from .. import config


def _optional(module: str, extra: str) -> Any:
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as exc:
        raise ImportError(
            f"{module} is required for this operation; install echr-py[{extra}]"
        ) from exc


def pdf_to_text(data: bytes) -> str:
    pymupdf = _optional("pymupdf", "exec-docs")
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in document).strip()
    finally:
        document.close()


def pdf_to_markdown(
    data: bytes, *, backend: Literal["pymupdf4llm", "markitdown"] = "pymupdf4llm"
) -> str:
    if backend == "markitdown":
        module = _optional("markitdown", "exec-docs")
        result = module.MarkItDown(enable_plugins=False).convert_stream(
            io.BytesIO(data), file_extension=".pdf"
        )
        return str(result.text_content).strip()
    module = _optional("pymupdf4llm", "exec-docs")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(data)
        handle.flush()
        return str(module.to_markdown(handle.name)).strip()


def convert_stream(data: bytes, *, file_type: str) -> str:
    """Convert PDF/DOCX with MarkItDown; HTML is handled without heavy deps."""
    suffix = file_type.lower().lstrip(".")
    if suffix in {"html", "htm"}:
        from ..text import html_to_text

        return html_to_text(data.decode("utf-8", errors="replace")).strip()
    module = _optional("markitdown", "exec-docs")
    result = module.MarkItDown(enable_plugins=False).convert_stream(
        io.BytesIO(data), file_extension=f".{suffix}"
    )
    return str(result.text_content).strip()


def looks_scanned(
    text: str,
    *,
    byte_length: int = 0,
    page_count: int | None = None,
    min_chars: int = 120,
) -> bool:
    """Conservatively identify a PDF whose text layer is absent or unusable.

    File size alone is deliberately not a signal: a valid one-page PDF can be
    large because of embedded fonts or images. Empty output, near-empty output,
    or very low text density across a known multi-page document triggers OCR.
    """
    normalized = re.sub(r"\s+", "", text or "")
    del byte_length  # retained for backward-compatible callers and diagnostics
    if len(normalized) >= min_chars:
        return False
    if len(normalized) < 20:
        return True
    return bool(page_count and page_count > 1 and len(normalized) / page_count < 40)


def _mistral_client(api_key: str) -> Any:
    """Create a Mistral client across the v1 and v2 SDK import layouts."""
    module = _optional("mistralai", "ocr")
    client_type = getattr(module, "Mistral", None)
    if client_type is None:
        client_module = _optional("mistralai.client", "ocr")
        client_type = getattr(client_module, "Mistral", None)
    if client_type is None:
        raise ImportError(
            "The installed mistralai package has no Mistral client; "
            "upgrade with: pip install -U 'echr-py[ocr]'"
        )
    return client_type(api_key=api_key)


def ocr_pdf(data: bytes, *, client: Any = None, model: str | None = None) -> str:
    """Run opt-in Mistral OCR and concatenate returned page Markdown."""
    if client is None:
        client = _mistral_client(config.get_mistral_api_key(required=True) or "")
    encoded = base64.b64encode(data).decode("ascii")
    response = client.ocr.process(
        model=model or config.MISTRAL_OCR_MODEL,
        document={"type": "document_url", "document_url": f"data:application/pdf;base64,{encoded}"},
    )
    pages = getattr(response, "pages", None) or []
    chunks = [str(getattr(page, "markdown", "") or "") for page in pages]
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def extract_text(
    source: bytes | str | Path,
    *,
    file_type: str | None = None,
    markdown: bool = False,
    markdown_backend: Literal["pymupdf4llm", "markitdown"] = "pymupdf4llm",
    ocr: bool = False,
    ocr_client: Any = None,
) -> str:
    """Extract text from bytes or a path, using OCR only when explicitly enabled."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        data = path.read_bytes()
        file_type = file_type or path.suffix
    else:
        data = source
    kind = (file_type or "pdf").lower().lstrip(".")
    if kind == "pdf":
        text = pdf_to_markdown(data, backend=markdown_backend) if markdown else pdf_to_text(data)
        if ocr and looks_scanned(text, byte_length=len(data)):
            return ocr_pdf(data, client=ocr_client)
        return text
    return convert_stream(data, file_type=kind)


__all__ = [
    "convert_stream",
    "extract_text",
    "looks_scanned",
    "ocr_pdf",
    "pdf_to_markdown",
    "pdf_to_text",
]
