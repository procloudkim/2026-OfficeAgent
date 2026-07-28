"""Document text extraction with source offsets."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from officeagent.domain.models import ExtractedSection


class UnsupportedDocumentError(ValueError):
    """Raised when a file extension is outside the supported contract."""


class EmptyDocumentError(ValueError):
    """Raised when extraction produces no searchable text."""


def normalize_filename(filename: str) -> str:
    """Remove client-side paths while preserving the visible file name."""

    normalized = Path(filename.replace("\\", "/")).name.strip()
    if not normalized or normalized in {".", ".."}:
        raise ValueError("A valid filename is required")
    return normalized


def extract_sections(filename: str, content: bytes) -> list[ExtractedSection]:
    """Extract UTF-8 text, Markdown, or PDF into provenance-aware sections."""

    suffix = Path(filename).suffix.casefold()
    if suffix in {".txt", ".md"}:
        sections = _extract_text(content)
    elif suffix == ".pdf":
        sections = _extract_pdf(content)
    else:
        raise UnsupportedDocumentError(
            f"Unsupported document format '{suffix or '<none>'}'. "
            "Supported formats: .pdf, .txt, .md"
        )

    if not sections or not any(section.text.strip() for section in sections):
        raise EmptyDocumentError("The document does not contain extractable text")
    return sections


def _extract_text(content: bytes) -> list[ExtractedSection]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Text documents must be UTF-8 encoded") from exc
    return [
        ExtractedSection(
            text=text,
            page_number=None,
            start_offset=0,
            end_offset=len(text),
        )
    ]


def _extract_pdf(content: bytes) -> list[ExtractedSection]:
    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        raise ValueError("The PDF could not be read") from exc

    sections: list[ExtractedSection] = []
    global_offset = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise ValueError(f"Text extraction failed on PDF page {page_number}") from exc
        sections.append(
            ExtractedSection(
                text=text,
                page_number=page_number,
                start_offset=global_offset,
                end_offset=global_offset + len(text),
            )
        )
        global_offset += len(text)
    return sections
