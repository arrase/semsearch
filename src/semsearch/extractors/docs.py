"""Document and text extractor supporting txt, md, html, docx, odt, and rtf."""

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
from docx import Document
from semsearch.core.types import ExtractedSegment, Modality, SegmentKind


@dataclass
class DocumentChunk:
    """Text chunk extracted from document."""

    text: str
    loc: int
    start_char: int = 0
    end_char: int = 0

    def to_segment(self) -> ExtractedSegment:
        """Convert chunk into an ExtractedSegment."""
        return ExtractedSegment(
            kind=SegmentKind.CHUNK,
            modality=Modality.TEXT,
            loc=float(self.loc),
            excerpt=self.text[:200],
            payload=self.text,
        )


def has_words(text: str, min_letter_ratio: float = 0.20) -> bool:
    """Check if at least min_letter_ratio of non-whitespace characters are letters."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return False
    letter_count = sum(1 for c in non_ws if c.isalpha())
    return (letter_count / len(non_ws)) >= min_letter_ratio


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pieces: list[str] = []

    def handle_data(self, data: str) -> None:
        self.pieces.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self.pieces.append("\n")


def strip_html(html_text: str) -> str:
    """Strip HTML tags and convert to plain text."""
    parser = _HTMLTextExtractor()
    parser.feed(html_text)
    text = "".join(parser.pieces)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _strip_rtf(rtf_text: str) -> str:
    """Strip RTF formatting control sequences to extract plain text."""
    pattern = re.compile(r"\{\\(?:fonttbl|colortbl|stylesheet|info|pict)[^{}]*\}", re.DOTALL)
    while pattern.search(rtf_text):
        rtf_text = pattern.sub("", rtf_text)
    rtf_text = re.sub(r"\\(?:par|line)\b", "\n", rtf_text)
    rtf_text = re.sub(r"\\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), rtf_text)
    rtf_text = re.sub(r"\\[a-zA-Z]+-?[0-9]*[ ]?", "", rtf_text)
    rtf_text = re.sub(r"[{}]", "", rtf_text)
    lines = [line.strip() for line in rtf_text.splitlines()]
    return "\n".join(line for line in lines if line)


def _read_odt(path: str) -> str:
    """Extract paragraph and heading text from OpenDocument Text (.odt) zip archive."""
    with zipfile.ZipFile(path) as zf:
        content_xml = zf.read("content.xml")
    tree = ET.fromstring(content_xml)
    paragraphs: list[str] = []
    for elem in tree.iter():
        if elem.tag.endswith("}p") or elem.tag.endswith("}h"):
            text = "".join(elem.itertext()).strip()
            if text:
                paragraphs.append(text)
    return "\n\n".join(paragraphs)


def read_doc_text(path: str) -> str:
    """Read plain text from various document formats (.txt, .md, .html, .docx, .odt, .rtf)."""
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in (".txt", ".md"):
        return p.read_text(encoding="utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        raw_html = p.read_text(encoding="utf-8", errors="replace")
        return strip_html(raw_html)
    if suffix == ".docx":
        doc = Document(path)
        parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            seen_cells = set()
            for row in table.rows:
                row_parts = []
                for cell in row.cells:
                    if cell._tc in seen_cells:
                        continue
                    seen_cells.add(cell._tc)
                    text = cell.text.strip()
                    if text:
                        row_parts.append(text)
                if row_parts:
                    parts.append("\t".join(row_parts))
        return "\n\n".join(parts)
    if suffix == ".odt":
        return _read_odt(path)
    if suffix == ".rtf":
        raw_rtf = p.read_text(encoding="utf-8", errors="replace")
        return _strip_rtf(raw_rtf)

    return p.read_text(encoding="utf-8", errors="replace")


def chunk_document(text: str, chunk_size: int = 1800, overlap: int = 200) -> list[DocumentChunk]:
    """Slice document text into chunks snapping to word boundaries and filtering non-text."""
    clean_text = text.strip()
    n = len(clean_text)
    if n == 0:
        return []

    if n <= chunk_size:
        if has_words(clean_text):
            return [DocumentChunk(text=clean_text, loc=0, start_char=0, end_char=n)]
        return []

    chunks: list[DocumentChunk] = []
    start = 0
    chunk_idx = 0

    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            search_start = max(start, end - overlap)
            cut = -1
            for i in range(end - 1, search_start - 1, -1):
                if clean_text[i].isspace():
                    cut = i
                    break
            if cut > start:
                end = cut

        chunk_str = clean_text[start:end].strip()
        if chunk_str and has_words(chunk_str):
            chunks.append(
                DocumentChunk(
                    text=chunk_str,
                    loc=chunk_idx,
                    start_char=start,
                    end_char=end,
                )
            )
            chunk_idx += 1

        if end >= n:
            break

        next_start = max(start + 1, end - overlap)
        while next_start < end and not clean_text[next_start].isspace():
            next_start += 1
        while next_start < n and clean_text[next_start].isspace():
            next_start += 1

        if next_start <= start:
            next_start = end

        start = next_start

    return chunks


def chunk_text(text: str, chunk_size: int = 1800, overlap: int = 200) -> list[tuple[int, int, str]]:
    """Return list of (start_char, end_char, chunk_text) tuples."""
    chunks = chunk_document(text, chunk_size=chunk_size, overlap=overlap)
    return [(c.start_char, c.end_char, c.text) for c in chunks]


def extract_doc_segments(path: str, chunk_size: int = 1800, overlap: int = 200) -> list[ExtractedSegment]:
    """Extract document file and return list of ExtractedSegment objects."""
    text = read_doc_text(path)
    chunks = chunk_document(text, chunk_size=chunk_size, overlap=overlap)
    return [chunk.to_segment() for chunk in chunks]


# Backward-compatible alias
extract_doc = extract_doc_segments
