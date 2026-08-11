"""Text extraction for the document formats supported by V1."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf", ".pptx", ".xlsx", ".eml", ".html", ".htm"}


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Extracted text and format-specific metadata."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def infer_document_type(path: Path) -> str:
    """Infer a stable document type from a file extension."""

    return {
        ".eml": "email",
        ".pdf": "pdf",
        ".docx": "document",
        ".xlsx": "spreadsheet",
        ".pptx": "presentation",
        ".md": "markdown",
        ".txt": "text",
        ".html": "html",
        ".htm": "html",
    }.get(path.suffix.lower(), "file")


def infer_mime_type(path: Path) -> str:
    """Return a MIME type, with sensible fallbacks for supported formats."""

    mime_type, _ = mimetypes.guess_type(path.name)
    return mime_type or "application/octet-stream"


def _decode_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_txt(path: Path) -> ExtractionResult:
    """Extract UTF-8 text, with BOM and Windows-code-page fallback handling."""

    return ExtractionResult(_decode_text(path.read_bytes()))


def _iter_docx_blocks(parent: Any) -> Iterable[Any]:
    """Yield paragraphs and tables in their XML document order."""

    from docx.document import Document as DocumentObject
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    parent_element = parent.element.body if isinstance(parent, DocumentObject) else parent._tc
    for child in parent_element.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, parent)
        elif child.tag.endswith("}tbl"):
            yield Table(child, parent)


def extract_docx(path: Path) -> ExtractionResult:
    """Extract paragraphs and table cells from a Word document in useful order."""

    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(path)
    sections: list[str] = []
    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            if block.text.strip():
                sections.append(block.text.strip())
        else:
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in block.rows]
            sections.extend(row for row in rows if row.strip())
    return ExtractionResult("\n".join(sections))


def extract_pdf(path: Path) -> ExtractionResult:
    """Extract text page by page from a PDF without OCR."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {number} ---\n{text.strip()}")
    return ExtractionResult("\n\n".join(pages))


def _shape_text(shape: Any) -> list[str]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        values: list[str] = []
        for child in shape.shapes:  # type: ignore[attr-defined]
            values.extend(_shape_text(child))
        return values
    if getattr(shape, "has_text_frame", False):
        value = shape.text.strip()
        return [value] if value else []
    return []


def extract_pptx(path: Path) -> ExtractionResult:
    """Extract text-bearing shapes from PowerPoint slides."""

    from pptx import Presentation

    presentation = Presentation(path)
    slides: list[str] = []
    for number, slide in enumerate(presentation.slides, start=1):
        values: list[str] = []
        for shape in slide.shapes:
            values.extend(_shape_text(shape))
        if values:
            slides.append(f"--- Slide {number} ---\n" + "\n".join(values))
    return ExtractionResult("\n\n".join(slides))


def extract_xlsx(path: Path) -> ExtractionResult:
    """Extract non-empty workbook cells with sheet names, without pandas."""

    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheets: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            rows: list[str] = []
            for row in worksheet.iter_rows(values_only=True):
                values = [str(value).strip() for value in row if value is not None and str(value).strip()]
                if values:
                    rows.append("\t".join(values))
            if rows:
                sheets.append(f"--- Sheet: {worksheet.title} ---\n" + "\n".join(rows))
    finally:
        workbook.close()
    return ExtractionResult("\n\n".join(sheets))


def _html_to_text(value: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)


def _email_body(message: Any) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart() or part.get_filename():
            continue
        content_type = part.get_content_type()
        content = part.get_content()
        if not isinstance(content, str):
            continue
        if content_type == "text/plain":
            plain_parts.append(content.strip())
        elif content_type == "text/html":
            html_parts.append(content)
    if plain_parts:
        return "\n\n".join(part for part in plain_parts if part)
    return "\n\n".join(_html_to_text(part) for part in html_parts if part)


def _addresses(header: str | None) -> list[str]:
    if not header:
        return []
    from email.utils import getaddresses

    return [address or name for name, address in getaddresses([header]) if address or name]


def extract_eml(path: Path) -> ExtractionResult:
    """Extract useful email headers and a readable body, excluding binary attachments."""

    with path.open("rb") as file:
        message = BytesParser(policy=policy.default).parse(file)
    attachment_names = [
        part.get_filename()
        for part in message.walk()
        if part.get_filename()
    ]
    metadata: dict[str, Any] = {
        "subject": str(message.get("subject") or "").strip(),
        "sender": str(message.get("from") or "").strip(),
        "recipients": _addresses(message.get("to")),
        "cc": _addresses(message.get("cc")),
        "sent_at": str(message.get("date") or "").strip(),
        "attachment_names": [name for name in attachment_names if name],
    }
    header_lines = [
        f"Subject: {metadata['subject']}" if metadata["subject"] else "",
        f"From: {metadata['sender']}" if metadata["sender"] else "",
        f"To: {', '.join(metadata['recipients'])}" if metadata["recipients"] else "",
        f"Cc: {', '.join(metadata['cc'])}" if metadata["cc"] else "",
        f"Date: {metadata['sent_at']}" if metadata["sent_at"] else "",
    ]
    header_text = "\n".join(line for line in header_lines if line)
    body = _email_body(message)
    text = "\n\n".join(part for part in (header_text, body) if part)
    return ExtractionResult(text, metadata)


def extract_html(path: Path) -> ExtractionResult:
    """Extract readable text from HTML when optional HTML support is used."""

    return ExtractionResult(_html_to_text(_decode_text(path.read_bytes())))


def extract_file(path: Path) -> ExtractionResult:
    """Dispatch extraction based on the lower-case file extension."""

    extractors = {
        ".txt": extract_txt,
        ".md": extract_txt,
        ".docx": extract_docx,
        ".pdf": extract_pdf,
        ".pptx": extract_pptx,
        ".xlsx": extract_xlsx,
        ".eml": extract_eml,
        ".html": extract_html,
        ".htm": extract_html,
    }
    try:
        extractor = extractors[path.suffix.lower()]
    except KeyError as error:
        raise ValueError(f"Unsupported file type: {path.suffix or '(none)'}") from error
    return extractor(path)
