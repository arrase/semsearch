"""Document and text extractor supporting txt, md, html, docx, odt, and rtf."""

import re
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from docx import Document
from docx.table import Table, _Cell

from semsearch.core.search_text import has_words
from semsearch.core.types import ExtractedSegment, Modality, SegmentKind

MAX_ARCHIVE_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_TEXT_FILE_BYTES = 10 * 1024 * 1024

_HTML_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})
_HTML_BLOCK_TAGS = frozenset({"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"})

_RTF_SKIP_GROUP_RE = re.compile(r"\{\s*\\(?:(?:\*)|(?:fonttbl|colortbl|stylesheet|info|pict)(?![a-zA-Z]))")
_RTF_UNICODE_RE = re.compile(r"\\u(-?\d+) ?(?:\\'[0-9a-fA-F]{2}|[^\\{}\r\n])?")
_RTF_HEX_RE = re.compile(r"\\'([0-9a-fA-F]{2})")
_RTF_UTF16_MODULO = 65536
_UNICODE_MAX = 0x10FFFF
_SURROGATE_START = 0xD800
_SURROGATE_END = 0xDFFF


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth += 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.pieces.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth == 0 and tag in _HTML_BLOCK_TAGS:
            self.pieces.append("\n")


def strip_html(html_text: str) -> str:
    """Strip HTML tags and convert to plain text."""
    parser = _HTMLTextExtractor()
    parser.feed(html_text)
    text = "".join(parser.pieces)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _find_matching_brace(text: str, start: int) -> int:
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _remove_rtf_ignorable_groups(rtf_text: str) -> str:
    parts: list[str] = []
    pos = 0
    for match in _RTF_SKIP_GROUP_RE.finditer(rtf_text):
        start = match.start()
        if start < pos:
            continue
        parts.append(rtf_text[pos:start])
        pos = _find_matching_brace(rtf_text, start)
    parts.append(rtf_text[pos:])
    return "".join(parts)


def _decode_rtf_unicode(match: re.Match[str]) -> str:
    code = int(match.group(1))
    if code < 0:
        code += _RTF_UTF16_MODULO
    if 0 <= code <= _UNICODE_MAX and not (_SURROGATE_START <= code <= _SURROGATE_END):
        return chr(code)
    return ""


def _decode_rtf_hex(match: re.Match[str]) -> str:
    return bytes([int(match.group(1), 16)]).decode("cp1252", errors="replace")


def _strip_rtf(rtf_text: str) -> str:
    """Strip RTF formatting control sequences to extract plain text."""
    rtf_text = _remove_rtf_ignorable_groups(rtf_text)
    rtf_text = re.sub(r"\\(?:par|line)\b", "\n", rtf_text)
    rtf_text = _RTF_UNICODE_RE.sub(_decode_rtf_unicode, rtf_text)
    rtf_text = _RTF_HEX_RE.sub(_decode_rtf_hex, rtf_text)
    rtf_text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", rtf_text)
    rtf_text = re.sub(r"[{}]", "", rtf_text)
    lines = [line.strip() for line in rtf_text.splitlines()]
    return "\n".join(line for line in lines if line)


def _validate_zip_archive_size(zf: zipfile.ZipFile) -> None:
    if sum(info.file_size for info in zf.infolist()) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ValueError("Archive exceeds maximum safe uncompressed size")


def _check_text_file_size(path: Path) -> None:
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        raise ValueError(f"Text file exceeds maximum safe size ({MAX_TEXT_FILE_BYTES} bytes)")


def _read_odt(path: str) -> str:
    """Extract paragraph and heading text from OpenDocument Text (.odt) zip archive."""
    with zipfile.ZipFile(path) as zf:
        _validate_zip_archive_size(zf)
        content_xml = zf.read("content.xml")
    tree = ET.fromstring(content_xml)
    paragraphs: list[str] = []
    for elem in tree.iter():
        if elem.tag.endswith(("}p", "}h")):
            text = "".join(elem.itertext()).strip()
            if text:
                paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_docx_table_rows(table: Table) -> list[str]:
    seen_cells: set[_Cell] = set()
    rows: list[str] = []
    for row in table.rows:
        row_parts: list[str] = []
        for cell in row.cells:
            if cell in seen_cells:
                continue
            seen_cells.add(cell)
            text = cell.text.strip()
            if text:
                row_parts.append(text)
        if row_parts:
            rows.append("\t".join(row_parts))
    return rows


def _read_docx(path: str) -> str:
    """Extract paragraph and table cell text from Word (.docx) document."""
    with zipfile.ZipFile(path) as zf:
        _validate_zip_archive_size(zf)
    doc = Document(path)
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        parts.extend(_extract_docx_table_rows(table))
    return "\n\n".join(parts)


def read_doc_text(path: str) -> str:
    """Read plain text from supported document formats (.txt, .md, .markdown, .rst, .html, .docx, .odt, .rtf)."""
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".doc":
        raise ValueError("Legacy binary .doc format is not supported")
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".odt":
        return _read_odt(path)

    _check_text_file_size(p)
    raw_text = p.read_text(encoding="utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        return strip_html(raw_text)
    if suffix == ".rtf":
        return _strip_rtf(raw_text)
    return raw_text


def _make_chunk_segment(text: str, loc: int) -> ExtractedSegment:
    return ExtractedSegment(
        kind=SegmentKind.CHUNK,
        modality=Modality.TEXT,
        loc=float(loc),
        excerpt=text[:200],
        payload=text,
    )


def _snap_chunk_end(clean_text: str, start: int, end: int, overlap: int) -> int:
    search_start = max(start, end - overlap)
    for i in range(end - 1, search_start - 1, -1):
        if clean_text[i].isspace():
            return i if i > start else end
    return end


def _advance_chunk_start(clean_text: str, start: int, end: int, overlap: int) -> int:
    n = len(clean_text)
    next_start = max(start + 1, end - overlap)
    while next_start < end and not clean_text[next_start].isspace():
        next_start += 1
    while next_start < n and clean_text[next_start].isspace():
        next_start += 1
    return next_start if next_start > start else end


def chunk_document(text: str, chunk_size: int = 1800, overlap: int = 200) -> list[ExtractedSegment]:
    """Slice document text into chunks snapping to word boundaries and filtering non-text."""
    clean_text = text.strip()
    n = len(clean_text)
    if n == 0:
        return []

    if n <= chunk_size:
        return [_make_chunk_segment(clean_text, 0)] if has_words(clean_text) else []

    chunks: list[ExtractedSegment] = []
    start = 0
    chunk_idx = 0

    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            end = _snap_chunk_end(clean_text, start, end, overlap)

        chunk_str = clean_text[start:end].strip()
        if chunk_str and has_words(chunk_str):
            chunks.append(_make_chunk_segment(chunk_str, chunk_idx))
            chunk_idx += 1

        if end >= n:
            break

        start = _advance_chunk_start(clean_text, start, end, overlap)

    return chunks


def extract_doc(path: str, chunk_size: int = 1800, overlap: int = 200) -> list[ExtractedSegment]:
    """Extract document file and return list of ExtractedSegment objects."""
    text = read_doc_text(path)
    return chunk_document(text, chunk_size=chunk_size, overlap=overlap)
