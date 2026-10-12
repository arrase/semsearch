"""Core domain data types, enums, and models for SemSearch."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re


class FileKind(str, Enum):
    """File classification kind."""

    SCREENSHOT = "screenshot"
    IMAGE = "image"
    PDF = "pdf"
    DOC = "doc"
    AUDIO = "audio"
    VIDEO = "video"
    CODE = "code"


class SegmentKind(str, Enum):
    """Searchable segment piece kind."""

    NAME = "name"
    IMAGE = "image"
    PAGE = "page"
    PAGE_IMAGE = "page_image"
    CHUNK = "chunk"
    AUDIO = "audio"
    FRAME = "frame"
    LINES = "lines"
    CELLS = "cells"
    OCR = "ocr"


class Modality(str, Enum):
    """Modality producing segment embedding vector."""

    NONE = "none"
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class FileState(str, Enum):
    """Lifecycle indexing state of a file."""

    PENDING = "pending"
    DONE = "done"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class EngineActivity(str, Enum):
    """Current engine activity status."""

    STARTING = "starting"
    SYNCING = "syncing"
    INDEXING = "indexing"
    FINISHING = "finishing"
    IDLE = "idle"
    PAUSED = "paused"


@dataclass
class Candidate:
    """File candidate discovered during crawl."""

    path: str
    kind: FileKind
    size: int
    mtime: float
    inode: int
    device: int


@dataclass
class FileRecord:
    """Indexed file record in database."""

    id: int
    path: str
    kind: FileKind
    size: int
    mtime: float
    inode: int
    device: int
    state: FileState
    detail: str = ""
    info: dict = field(default_factory=dict)
    indexed_at: float = 0.0


@dataclass
class SegmentRecord:
    """Searchable segment record with embedding vector."""

    id: int
    file_id: int
    kind: SegmentKind
    modality: Modality
    loc: float = 0.0
    loc_end: float = 0.0
    excerpt: str = ""
    vec: bytes = b""


@dataclass
class SearchHit:
    """Scored search result hit from engine."""

    path: str
    kind: FileKind
    score: float
    z: float = 0.0
    cosine: float = 0.0
    keyword_rank: int = 0
    segment: SegmentKind = SegmentKind.NAME
    segment_id: int = 0
    loc: float = 0.0
    loc_end: float = 0.0
    excerpt: str = ""
    modified: float = 0.0
    size: int = 0
    info: dict = field(default_factory=dict)
    focus: float = 0.0


@dataclass
class ResultRow:
    """View model representing a search hit in the GUI."""

    title: str = ""
    folder: str = ""
    place: str = ""
    reason: str = ""
    details: str = ""
    found_words: list[str] = field(default_factory=list)
    matched_meaning: bool = False
    matched_words: bool = False
    code_line: int | None = None

    @classmethod
    def from_hit(cls, hit: SearchHit, terms: list[str] | None = None) -> "ResultRow":
        """Build ResultRow from a SearchHit."""
        file_path = Path(hit.path)
        title = file_path.name
        folder = str(file_path.parent)

        place = ""
        if hit.loc > 0:
            if hit.segment in (SegmentKind.PAGE, SegmentKind.PAGE_IMAGE):
                place = f"p. {int(hit.loc)}"
            elif hit.segment == SegmentKind.LINES:
                end = int(hit.loc_end) if hit.loc_end > 0 else int(hit.loc)
                place = f"L{int(hit.loc)}" if end == int(hit.loc) else f"L{int(hit.loc)}–{end}"
            elif hit.segment in (SegmentKind.AUDIO, SegmentKind.FRAME):
                minutes = int(hit.loc) // 60
                seconds = int(hit.loc) % 60
                place = f"{minutes}:{seconds:02d}"

        found_words: list[str] = []
        if hit.excerpt:
            matches = re.findall(r"«(.*?)»", hit.excerpt)
            for m in matches:
                clean = m.strip()
                if clean and clean not in found_words:
                    found_words.append(clean)

        matched_meaning = hit.z >= 2.0
        matched_words = hit.keyword_rank > 0

        reason = "Matches meaning" if matched_meaning else ("Matches words" if matched_words else "Matches name")
        details = hit.excerpt

        code_line = int(hit.focus) if hit.focus > 0 else (int(hit.loc) if hit.kind == FileKind.CODE and hit.loc > 0 else None)

        return cls(
            title=title,
            folder=folder,
            place=place,
            reason=reason,
            details=details,
            found_words=found_words,
            matched_meaning=matched_meaning,
            matched_words=matched_words,
            code_line=code_line,
        )


@dataclass
class EngineStatus:
    """Current engine operational state and counters."""

    activity: EngineActivity = EngineActivity.STARTING
    pending: int = 0
    indexed: int = 0
    total: int = 0
    searchable: int = 0
    files: int = 0
    vectors: int = 0
    paused: bool = False
    waiting_for: str = ""
    message: str = ""
    current_file: str = ""
