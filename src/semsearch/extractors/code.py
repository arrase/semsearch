"""Source code and Jupyter notebook extractor."""

import json
import re
from pathlib import Path

from semsearch.core.code_lookup import code_summary
from semsearch.core.types import ExtractedSegment, Modality, SegmentKind

DEFINITION_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|fn|function|func|pub\s+fn|struct|interface|type|trait|enum)\s+(\w+)"
)


def _find_focus(lines: list[str], start_line_num: int) -> int:
    """Find line number of the first symbol definition in the given lines."""
    for idx, line in enumerate(lines):
        if DEFINITION_RE.match(line):
            return start_line_num + idx
    return 0


def _make_code_segment(lines: list[str], loc: int, loc_end: int) -> ExtractedSegment:
    text = "".join(lines)
    focus = _find_focus(lines, loc)
    return ExtractedSegment(
        kind=SegmentKind.LINES,
        modality=Modality.TEXT,
        loc=float(loc),
        loc_end=float(loc_end),
        excerpt=code_summary(text),
        payload=text,
        focus=float(focus),
    )


def _find_natural_boundary(lines: list[str], start_idx: int, end_idx: int) -> int:
    search_floor = max(start_idx + 1, end_idx - 10)
    for j in range(end_idx - 1, search_floor - 1, -1):
        line = lines[j]
        stripped = line.strip()
        if not stripped or (not line[0].isspace() and stripped[0] not in "})]"):
            return j
    return -1


def _chunk_lines(lines: list[str], chunk_chars: int = 1800, overlap_lines: int = 2) -> list[ExtractedSegment]:
    """Chunk source lines into whole-line stretches with natural boundary snapping."""
    if not lines:
        return []

    segments: list[ExtractedSegment] = []
    n = len(lines)
    start_idx = 0

    while start_idx < n:
        accum_len = 0
        end_idx = start_idx

        while end_idx < n and accum_len < chunk_chars:
            accum_len += len(lines[end_idx])
            end_idx += 1

        if end_idx >= n:
            stretch_lines = lines[start_idx:n]
            if stretch_lines:
                segments.append(_make_code_segment(stretch_lines, start_idx + 1, n))
            break

        # Search backwards for natural boundary (blank line or top-level indent, excluding closing braces)
        cut_idx = _find_natural_boundary(lines, start_idx, end_idx)
        if cut_idx > start_idx:
            actual_end = cut_idx
            next_start = cut_idx
        else:
            # Unnatural split: cut at end_idx and hand over overlap_lines
            actual_end = end_idx
            next_start = max(start_idx + 1, end_idx - overlap_lines)

        stretch_lines = lines[start_idx:actual_end]
        if stretch_lines:
            segments.append(_make_code_segment(stretch_lines, start_idx + 1, actual_end))

        start_idx = next_start

    return segments


def extract_code(path: str, chunk_chars: int = 1800, overlap_lines: int = 2) -> list[ExtractedSegment]:
    """Extract code file into whole-line stretches with symbol definition detection."""
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    return _chunk_lines(lines, chunk_chars=chunk_chars, overlap_lines=overlap_lines)


def _parse_notebook_cells(cells: list[dict[str, object]]) -> list[tuple[int, str]]:
    numbered_cells: list[tuple[int, str]] = []
    for idx, cell in enumerate(cells, start=1):
        if cell.get("cell_type") not in ("code", "markdown"):
            continue
        source = cell.get("source", [])
        cell_text = "".join(str(line) for line in source) if isinstance(source, list) else str(source)
        if not cell_text.strip():
            continue
        if not cell_text.endswith("\n"):
            cell_text += "\n"
        numbered_cells.append((idx, cell_text))
    return numbered_cells


def _make_cells_segment(batch_texts: list[str], first_cell: int, last_cell: int) -> ExtractedSegment:
    combined = "\n".join(batch_texts)
    return ExtractedSegment(
        kind=SegmentKind.CELLS,
        modality=Modality.TEXT,
        loc=float(first_cell),
        loc_end=float(last_cell),
        excerpt=code_summary(combined),
        payload=combined,
        focus=0.0,
    )


def extract_notebook(path: str, chunk_chars: int = 1800) -> list[ExtractedSegment]:
    """Extract code and markdown cells from Jupyter Notebook as 1-based CELLS segments, stripping outputs."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    data = json.loads(raw)
    numbered_cells = _parse_notebook_cells(data.get("cells", []))
    if not numbered_cells:
        return []

    segments: list[ExtractedSegment] = []
    batch_texts: list[str] = []
    batch_len = 0
    first_cell = numbered_cells[0][0]
    last_cell = first_cell

    for cell_num, text in numbered_cells:
        if batch_texts and batch_len + len(text) > chunk_chars:
            segments.append(_make_cells_segment(batch_texts, first_cell, last_cell))
            batch_texts = []
            batch_len = 0
            first_cell = cell_num

        if not batch_texts:
            first_cell = cell_num
        batch_texts.append(text)
        batch_len += len(text)
        last_cell = cell_num

    if batch_texts:
        segments.append(_make_cells_segment(batch_texts, first_cell, last_cell))

    return segments
