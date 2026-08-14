"""Deterministic paragraph-aware chunking for long source text."""

from __future__ import annotations


def chunk_text(text: str | None, *, max_chars: int = 2400, overlap: int = 220) -> list[str]:
    """Return stable chunks without splitting ordinary paragraphs unnecessarily."""

    normalized = "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").splitlines()).strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in normalized.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                end = min(start + max_chars, len(paragraph))
                if end < len(paragraph):
                    boundary = paragraph.rfind(" ", start, end)
                    if boundary > start + max_chars // 2:
                        end = boundary
                chunks.append(paragraph[start:end].strip())
                if end >= len(paragraph):
                    break
                start = max(end - overlap, start + 1)
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            tail = current[-overlap:].strip() if overlap else ""
            current = f"{tail}\n\n{paragraph}" if tail else paragraph
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]
