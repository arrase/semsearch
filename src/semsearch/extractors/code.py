"""Source code and Jupyter notebook extractor."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from semsearch.core.types import ExtractedSegment, Modality, SegmentKind


@dataclass
class CodeStretch:
    """Whole-line stretch of source code."""

    text: str
    loc: int
    loc_end: int
    focus: int = 0

    def to_segment(self) -> ExtractedSegment:
        """Convert code stretch into an ExtractedSegment."""
        return ExtractedSegment(
            kind=SegmentKind.LINES,
            modality=Modality.TEXT,
            loc=float(self.loc),
            loc_end=float(self.loc_end),
            excerpt=self.text[:200],
            payload=self.text,
            focus=float(self.focus),
        )


DEFINITION_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|fn|function|func|pub\s+fn|struct|interface|type|trait|enum)\s+([a-zA-Z0-9_]+)"
)


def _find_focus(lines: list[str], start_line_num: int) -> int:
    """Find line number of the first symbol definition in the given lines."""
    for idx, line in enumerate(lines):
        if DEFINITION_RE.match(line):
            return start_line_num + idx
    return 0


def _chunk_lines(lines: list[str], chunk_chars: int = 1800, overlap_lines: int = 2) -> list[CodeStretch]:
    """Chunk source lines into whole-line stretches with natural boundary snapping."""
    if not lines:
        return []

    stretches: list[CodeStretch] = []
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
                focus = _find_focus(stretch_lines, start_idx + 1)
                stretches.append(
                    CodeStretch(
                        text="".join(stretch_lines),
                        loc=start_idx + 1,
                        loc_end=n,
                        focus=focus,
                    )
                )
            break

        # Search backwards for natural boundary (blank line or top-level indent)
        cut_idx = -1
        search_floor = max(start_idx + 1, end_idx - 10)
        for j in range(end_idx - 1, search_floor - 1, -1):
            line = lines[j]
            is_blank = not line.strip()
            is_toplevel = bool(line.strip()) and not line[0].isspace()
            if is_blank or is_toplevel:
                cut_idx = j
                break

        if cut_idx > start_idx:
            actual_end = cut_idx
            next_start = cut_idx
        else:
            # Unnatural split: cut at end_idx and hand over overlap_lines
            actual_end = end_idx
            next_start = max(start_idx + 1, end_idx - overlap_lines)

        stretch_lines = lines[start_idx:actual_end]
        if stretch_lines:
            focus = _find_focus(stretch_lines, start_idx + 1)
            stretches.append(
                CodeStretch(
                    text="".join(stretch_lines),
                    loc=start_idx + 1,
                    loc_end=actual_end,
                    focus=focus,
                )
            )

        start_idx = next_start

    return stretches


def extract_code(path: str, chunk_chars: int = 1800, overlap_lines: int = 2) -> list[CodeStretch]:
    """Extract code file into whole-line stretches with symbol definition detection."""
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    return _chunk_lines(lines, chunk_chars=chunk_chars, overlap_lines=overlap_lines)


def extract_notebook(path: str, chunk_chars: int = 1800) -> list[CodeStretch]:
    """Extract code and markdown cells from Jupyter Notebook, strictly stripping outputs."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    data = json.loads(raw)

    cells = data.get("cells", [])
    extracted_lines: list[str] = []

    for cell in cells:
        cell_type = cell.get("cell_type")
        if cell_type not in ("code", "markdown"):
            continue

        source = cell.get("source", [])
        if isinstance(source, list):
            cell_text = "".join(source)
        else:
            cell_text = str(source)

        if not cell_text.strip():
            continue

        for line in cell_text.splitlines(keepends=True):
            extracted_lines.append(line)
        if not extracted_lines[-1].endswith("\n"):
            extracted_lines[-1] += "\n"
        extracted_lines.append("\n")

    return _chunk_lines(extracted_lines, chunk_chars=chunk_chars)


def extract_code_segments(path: str) -> list[ExtractedSegment]:
    """Extract code file and return list of ExtractedSegment objects."""
    stretches = extract_code(path)
    return [s.to_segment() for s in stretches]


def extract_notebook_segments(path: str) -> list[ExtractedSegment]:
    """Extract notebook file and return list of ExtractedSegment objects."""
    stretches = extract_notebook(path)
    return [s.to_segment() for s in stretches]
