"""Core domain data types, enums, and models for SemSearch."""

from dataclasses import dataclass, field
from enum import Enum

from semsearch.i18n import tr


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
class ExtractedSegment:
    """Searchable segment piece extracted from a file."""

    kind: SegmentKind
    modality: Modality
    loc: float = 0.0
    loc_end: float = 0.0
    excerpt: str = ""
    payload: str | dict[str, str] = ""
    focus: float = 0.0


SEMANTIC_Z_GATE = 2.2


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

    @property
    def reason(self) -> str:
        """Human-readable explanation for why this hit matched."""
        if self.z >= SEMANTIC_Z_GATE:
            return tr("Matches meaning")
        if self.keyword_rank > 0:
            return tr("Matches words")
        return tr("Matches name")


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
