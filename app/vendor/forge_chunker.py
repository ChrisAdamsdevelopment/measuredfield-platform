from __future__ import annotations

import re

_HEADER_SPLIT_RE = re.compile(r"(?=^#{1,6}\s+.+$)", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    step = max(1, chunk_size - overlap)
    chunks: list[str] = []
    for start in range(0, len(text), step):
        end = min(len(text), start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
    return chunks


def chunk_document(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into overlapping chunks with markdown/paragraph/sentence awareness."""
    text = (text or "").strip()
    if not text:
        return []

    sections = (
        [s.strip() for s in _HEADER_SPLIT_RE.split(text) if s.strip()]
        if "#" in text
        else [text]
    )
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            chunks.extend(_split_long_text(current.strip(), chunk_size, overlap))
            current = ""

    for section in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
        for para in paragraphs:
            candidate = f"{current}\n\n{para}".strip() if current else para
            if len(candidate) <= chunk_size:
                current = candidate
                continue

            flush_current()
            if len(para) <= chunk_size:
                current = para
                continue

            sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(para) if s.strip()]
            sentence_buf = ""
            for sentence in sentences:
                sentence_candidate = (
                    f"{sentence_buf} {sentence}".strip() if sentence_buf else sentence
                )
                if len(sentence_candidate) <= chunk_size:
                    sentence_buf = sentence_candidate
                else:
                    if sentence_buf:
                        chunks.extend(
                            _split_long_text(sentence_buf, chunk_size, overlap)
                        )
                    if len(sentence) <= chunk_size:
                        sentence_buf = sentence
                    else:
                        chunks.extend(_split_long_text(sentence, chunk_size, overlap))
                        sentence_buf = ""
            if sentence_buf:
                chunks.extend(_split_long_text(sentence_buf, chunk_size, overlap))

    flush_current()

    # enforce overlap between neighboring chunks when possible
    if overlap > 0 and len(chunks) > 1:
        with_overlap: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = with_overlap[-1]
            prefix = prev[-overlap:] if len(prev) > overlap else prev
            merged = f"{prefix}\n{chunks[i]}" if prefix else chunks[i]
            with_overlap.append(merged[: chunk_size + overlap])
        return with_overlap

    return chunks
