"""SQLite WAL database management, RAM vector snapshot cache, and full-text search indexing."""

import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, TypedDict

import numpy as np
import numpy.typing as npt

from semsearch.core.search_text import extract_snippet, normalize_search_text
from semsearch.core.types import (
    Candidate,
    FileKind,
    FileRecord,
    FileState,
    Modality,
    SegmentKind,
)
from semsearch.core.vectors import CODE_TO_MODALITY, MODALITY_TO_CODE, bytes_to_float16

SCHEMA_VERSION = 6
MAIN_DB_FILE = "index.sqlite"
CODE_DB_FILE = "code.sqlite"
FILE_COLUMNS = "id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at"
_MIN_FTS_WORD_LEN = 2
_SQLITE_MAX_VARS = 900
_PREBUILT_FTS_EXPR_RE = re.compile(r'^(?:"(?:[^"\x00]|"")+"\*?)(?:\s+(?:OR|AND)\s+"(?:[^"\x00]|"")+"\*?)*$')


class FtsMatchRow(TypedDict):
    """Structured result row from FTS5 BM25 search."""

    segment_id: int
    file_id: int | None
    name: str
    body: str
    original: str
    rank: float
    snippet: str
    loc: float
    excerpt: str


@dataclass(frozen=True)
class SegmentMeta:
    """Metadata describing a stored content segment."""

    id: int
    kind: SegmentKind
    loc: float
    loc_end: float
    excerpt: str
    focus: float = 0.0


@dataclass(frozen=True)
class VectorSnapshot:
    """Immutable RAM snapshot of all indexed embedding vectors and scale masks."""

    segment_ids: npt.NDArray[np.int64]
    file_ids: npt.NDArray[np.int64]
    modality_codes: npt.NDArray[np.uint8]
    matrix: npt.NDArray[np.float32]
    scale_mask: npt.NDArray[np.bool_]


def _empty_vector_snapshot() -> VectorSnapshot:
    seg_ids = np.empty((0,), dtype=np.int64)
    file_ids = np.empty((0,), dtype=np.int64)
    mod_codes = np.empty((0,), dtype=np.uint8)
    matrix = np.empty((0, 0), dtype=np.float32)
    scale_mask = np.empty((0,), dtype=np.bool_)
    for arr in (seg_ids, file_ids, mod_codes, matrix, scale_mask):
        arr.setflags(write=False)
    return VectorSnapshot(
        segment_ids=seg_ids,
        file_ids=file_ids,
        modality_codes=mod_codes,
        matrix=matrix,
        scale_mask=scale_mask,
    )


def _folder_text(path_obj: Path) -> str:
    parent = path_obj.parent
    home = Path.home()
    rel_parent = parent.relative_to(home) if parent.is_relative_to(home) else parent
    return " ".join(part for part in rel_parent.parts if part not in ("", "/"))


def _name_and_folder(path: str) -> tuple[str, str]:
    file_path = Path(path)
    return normalize_search_text(file_path.name), normalize_search_text(_folder_text(file_path))


def _chunked(items: list[int], size: int = _SQLITE_MAX_VARS) -> Iterator[list[int]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


class Database:
    """SQLite WAL storage manager with thread-local readers, atomic transactions, and RAM vector cache."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._is_memory = self.path == ":memory:"
        if not self._is_memory:
            resolved_path = Path(self.path).resolve()
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            self._read_uri = f"{resolved_path.as_uri()}?mode=ro"
        else:
            self._read_uri = ""

        self._write_lock = threading.RLock()
        self._tx_depth = 0
        self._vec_dirty_in_tx = False
        self._write_conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)

        self._read_local = threading.local()
        self._read_conns_lock = threading.Lock()
        self._read_conns: list[sqlite3.Connection] = []

        self._vec_cache_lock = threading.Lock()
        self._vec_generation = 0
        self._vec_cache_gen = -1
        self._vec_cache: VectorSnapshot | None = None

        self._init_db()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Execute one or more write statements inside a nested atomic IMMEDIATE transaction."""
        with self._write_lock:
            is_outer = self._tx_depth == 0
            cursor = self._write_conn.cursor()
            if is_outer:
                cursor.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield cursor
            except BaseException:
                self._tx_depth -= 1
                if is_outer:
                    self._vec_dirty_in_tx = False
                    if self._write_conn.in_transaction:
                        self._write_conn.execute("ROLLBACK")
                raise
            else:
                self._tx_depth -= 1
                if is_outer:
                    self._write_conn.execute("COMMIT")
                    if self._vec_dirty_in_tx:
                        self._vec_dirty_in_tx = False
                        with self._vec_cache_lock:
                            self._vec_generation += 1

    def _read_conn(self) -> sqlite3.Connection:
        if self._is_memory:
            return self._write_conn
        conn: sqlite3.Connection | None = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._read_uri, uri=True, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("PRAGMA query_only = ON;")
            self._read_local.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    @contextmanager
    def _read_cursor(self) -> Iterator[sqlite3.Cursor]:
        if self._is_memory:
            with self._write_lock:
                yield self._write_conn.cursor()
        else:
            yield self._read_conn().cursor()

    @staticmethod
    def _migrate_schema(cursor: sqlite3.Cursor) -> None:
        existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(segments);").fetchall()}
        if "focus" not in existing_cols:
            cursor.execute("ALTER TABLE segments ADD COLUMN focus REAL NOT NULL DEFAULT 0.0;")
        if "has_vec" not in existing_cols:
            cursor.execute("ALTER TABLE segments ADD COLUMN has_vec INTEGER NOT NULL DEFAULT 0;")
            cursor.execute("UPDATE segments SET has_vec = 1 WHERE vec IS NOT NULL;")

    def _init_db(self) -> None:
        with self._write_lock:
            self._write_conn.execute("PRAGMA journal_mode = WAL;")
            self._write_conn.execute("PRAGMA synchronous = NORMAL;")
            self._write_conn.execute("PRAGMA foreign_keys = ON;")
            self._write_conn.execute("PRAGMA busy_timeout = 5000;")
            current_version = int(self._write_conn.execute("PRAGMA user_version;").fetchone()[0])

            with self.transaction() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS files (
                        id INTEGER PRIMARY KEY,
                        path TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        mtime REAL NOT NULL,
                        inode INTEGER NOT NULL,
                        device INTEGER NOT NULL,
                        state TEXT NOT NULL DEFAULT 'pending',
                        detail TEXT,
                        info TEXT,
                        indexed_at REAL
                    );
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS segments (
                        id INTEGER PRIMARY KEY,
                        file INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        modality TEXT NOT NULL,
                        loc REAL NOT NULL DEFAULT 0.0,
                        loc_end REAL NOT NULL DEFAULT 0.0,
                        excerpt TEXT NOT NULL DEFAULT '',
                        focus REAL NOT NULL DEFAULT 0.0,
                        has_vec INTEGER NOT NULL DEFAULT 0,
                        vec BLOB
                    );
                    """
                )
                cursor.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
                        name,
                        body,
                        original UNINDEXED,
                        tokenize = "porter unicode61 remove_diacritics 2 categories 'L* N* Co M*'"
                    );
                    """
                )
                if 0 < current_version < SCHEMA_VERSION:
                    self._migrate_schema(cursor)

                cursor.execute("CREATE INDEX IF NOT EXISTS files_inode ON files(device, inode);")
                cursor.execute("CREATE INDEX IF NOT EXISTS files_state ON files(state, kind);")
                cursor.execute("CREATE INDEX IF NOT EXISTS files_state_mtime ON files(state, mtime DESC);")
                cursor.execute("CREATE INDEX IF NOT EXISTS segments_file ON segments(file);")
                cursor.execute("CREATE INDEX IF NOT EXISTS segments_has_vec ON segments(has_vec);")
                cursor.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS segments_fts_delete AFTER DELETE ON segments
                    BEGIN
                        DELETE FROM fts WHERE rowid = old.id;
                    END;
                    """
                )
                cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")

    def close(self) -> None:
        """Close thread-local read connections and the primary write connection."""
        with self._read_conns_lock:
            for conn in self._read_conns:
                conn.close()
            self._read_conns.clear()
        self._read_local = threading.local()
        with self._write_lock:
            self._write_conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def vacuum(self) -> None:
        """Compact SQLite database file."""
        with self._write_lock:
            self._write_conn.execute("VACUUM")

    def shrink_memory(self) -> None:
        """Release non-essential SQLite heap and page cache allocations."""
        with self._write_lock:
            self._write_conn.execute("PRAGMA shrink_memory;")

    def get_meta(self, key: str) -> str | None:
        """Retrieve metadata value by key."""
        with self._read_cursor() as cursor:
            cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
            row = cursor.fetchone()
            return str(row[0]) if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        """Set or remove metadata key/value."""
        with self.transaction() as cursor:
            self._set_meta_in_tx(cursor, key, value)

    @staticmethod
    def _set_meta_in_tx(cursor: sqlite3.Cursor, key: str, value: str | None) -> None:
        if value is None:
            cursor.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            cursor.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def sync_model_metadata(self, model: str, dim: int = 0) -> bool:
        """Record active embedding model/dimension, resetting index if incompatible with stored fingerprint."""
        with self.transaction() as cursor:
            cursor.execute("SELECT key, value FROM meta WHERE key IN ('embedding_model', 'embedding_dim')")
            stored = dict(cursor.fetchall())
            stored_model: str | None = stored.get("embedding_model")
            stored_dim_raw = stored.get("embedding_dim", "")
            stored_dim = int(stored_dim_raw) if stored_dim_raw.isdigit() else 0

            model_changed = stored_model is not None and stored_model != model
            dim_changed = dim > 0 and stored_dim > 0 and stored_dim != dim
            if model_changed or dim_changed:
                has_indexed = (
                    cursor.execute("SELECT 1 FROM segments WHERE kind != 'name' LIMIT 1").fetchone() is not None
                    or cursor.execute("SELECT 1 FROM files WHERE state != 'pending' LIMIT 1").fetchone() is not None
                )
                if has_indexed:
                    self.reset_all_for_reindex()
                self._set_meta_in_tx(cursor, "embedding_model", model)
                self._set_meta_in_tx(cursor, "embedding_dim", str(dim) if dim > 0 else None)
                return has_indexed

            if stored_model is None:
                self._set_meta_in_tx(cursor, "embedding_model", model)
            if dim > 0 and stored_dim == 0:
                self._set_meta_in_tx(cursor, "embedding_dim", str(dim))
            return False

    def reset_all_for_reindex(self) -> int:
        """Delete all non-name segments and reset all files to pending for full re-indexing."""
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM segments WHERE kind != 'name'")
            cursor.execute(
                """
                UPDATE files
                SET state = 'pending', detail = NULL, info = NULL, indexed_at = NULL
                """
            )
            reset_count = cursor.rowcount
            self._vec_dirty_in_tx = True
            return reset_count

    def delete_non_name_segments(self, file_id: int) -> None:
        """Delete all extracted non-name segments (and their FTS entries via trigger) for a file."""
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM segments WHERE file = ? AND kind != 'name'", (file_id,))
            self._vec_dirty_in_tx = True

    def insert_file(self, candidate: Candidate) -> int:
        """Insert a discovered file candidate as pending and create its name segment and FTS entry."""
        name, folder = _name_and_folder(candidate.path)
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO files (path, kind, size, mtime, inode, device, state)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    candidate.path,
                    candidate.kind.value,
                    candidate.size,
                    candidate.mtime,
                    candidate.inode,
                    candidate.device,
                ),
            )
            file_id = int(cursor.lastrowid or 0)
            cursor.execute(
                """
                INSERT INTO segments (file, kind, modality, loc, loc_end, excerpt, focus, has_vec, vec)
                VALUES (?, ?, ?, 0.0, 0.0, '', 0.0, 0, NULL)
                """,
                (file_id, SegmentKind.NAME.value, Modality.NONE.value),
            )
            segment_id = int(cursor.lastrowid or 0)
            cursor.execute(
                """
                INSERT INTO fts (rowid, name, body, original)
                VALUES (?, ?, ?, '')
                """,
                (segment_id, name, folder),
            )
            return file_id

    def requeue_file(self, file_id: int, candidate: Candidate) -> None:
        """Requeue a changed file, clearing extracted segments and refreshing name/folder FTS."""
        name, folder = _name_and_folder(candidate.path)
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM segments WHERE file = ? AND kind != 'name'", (file_id,))
            self._vec_dirty_in_tx = True
            cursor.execute(
                """
                UPDATE files
                SET path = ?, kind = ?, size = ?, mtime = ?, inode = ?, device = ?,
                    state = 'pending', detail = NULL, info = NULL, indexed_at = NULL
                WHERE id = ?
                """,
                (
                    candidate.path,
                    candidate.kind.value,
                    candidate.size,
                    candidate.mtime,
                    candidate.inode,
                    candidate.device,
                    file_id,
                ),
            )
            cursor.execute(
                """
                UPDATE fts
                SET name = ?, body = ?
                WHERE rowid IN (SELECT id FROM segments WHERE file = ? AND kind = 'name')
                """,
                (name, folder, file_id),
            )

    def update_file_kind(self, file_id: int, kind: FileKind) -> None:
        """Update the classified FileKind of an existing file record."""
        with self.transaction() as cursor:
            cursor.execute("UPDATE files SET kind = ? WHERE id = ?", (kind.value, file_id))

    def move_file(self, file_id: int, new_path: str) -> None:
        """Update file path and its FTS name row without mutating segments or vectors."""
        new_name, new_folder = _name_and_folder(new_path)
        with self.transaction() as cursor:
            cursor.execute("UPDATE files SET path = ? WHERE id = ?", (new_path, file_id))
            cursor.execute(
                """
                UPDATE fts
                SET name = ?, body = ?
                WHERE rowid IN (SELECT id FROM segments WHERE file = ? AND kind = 'name')
                """,
                (new_name, new_folder, file_id),
            )

    def delete_file(self, file_id: int) -> None:
        """Delete file record with cascading deletion to segments and FTS."""
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
            self._vec_dirty_in_tx = True

    def find_by_path(self, path: str) -> FileRecord | None:
        """Find a file record by exact path."""
        with self._read_cursor() as cursor:
            cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE path = ?", (path,))
            row = cursor.fetchone()
            return self._row_to_file_record(row) if row else None

    def find_by_id(self, file_id: int) -> FileRecord | None:
        """Find a file record by file ID."""
        with self._read_cursor() as cursor:
            cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE id = ?", (file_id,))
            row = cursor.fetchone()
            return self._row_to_file_record(row) if row else None

    def find_by_ids(self, file_ids: Iterable[int]) -> dict[int, FileRecord]:
        """Batch-fetch file records keyed by file ID."""
        unique_ids = list(dict.fromkeys(file_ids))
        if not unique_ids:
            return {}
        records: dict[int, FileRecord] = {}
        with self._read_cursor() as cursor:
            for chunk in _chunked(unique_ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE id IN ({placeholders})", chunk)
                for row in cursor.fetchall():
                    rec = self._row_to_file_record(row)
                    records[rec.id] = rec
        return records

    def find_by_inode(self, device: int, inode: int) -> FileRecord | None:
        """Find a file record by device and inode."""
        with self._read_cursor() as cursor:
            cursor.execute(
                f"SELECT {FILE_COLUMNS} FROM files WHERE device = ? AND inode = ?",
                (device, inode),
            )
            row = cursor.fetchone()
            return self._row_to_file_record(row) if row else None

    def find_all_files(self) -> list[FileRecord]:
        """Retrieve all file records."""
        with self._read_cursor() as cursor:
            cursor.execute(f"SELECT {FILE_COLUMNS} FROM files")
            return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def find_files_under(self, root: str) -> list[FileRecord]:
        """Query all files under a specific root prefix."""
        root_path = str(Path(root).resolve())
        escaped_root = root_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"{escaped_root}/%" if escaped_root != "/" else "/%"
        with self._read_cursor() as cursor:
            cursor.execute(
                f"SELECT {FILE_COLUMNS} FROM files WHERE path = ? OR path LIKE ? ESCAPE '\\'",
                (root_path, pattern),
            )
            return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def list_pending(self, limit: int = 0) -> list[FileRecord]:
        """List pending files prioritized by file kind and modification time."""
        sql = f"""
            SELECT {FILE_COLUMNS}
            FROM files
            WHERE state = 'pending'
            ORDER BY
                CASE kind
                    WHEN 'screenshot' THEN 1
                    WHEN 'image' THEN 2
                    WHEN 'pdf' THEN 3
                    WHEN 'doc' THEN 4
                    WHEN 'code' THEN 5
                    WHEN 'audio' THEN 6
                    WHEN 'video' THEN 7
                    ELSE 8
                END,
                mtime DESC
        """
        with self._read_cursor() as cursor:
            if limit > 0:
                cursor.execute(f"{sql} LIMIT ?", (limit,))
            else:
                cursor.execute(sql)
            return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def find_partial(self) -> FileRecord | None:
        """Find the most recently modified partially indexed file."""
        with self._read_cursor() as cursor:
            cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE state = 'partial' ORDER BY mtime DESC LIMIT 1")
            row = cursor.fetchone()
            return self._row_to_file_record(row) if row else None

    def insert_segment(
        self,
        file_id: int,
        kind: SegmentKind,
        modality: Modality,
        loc: float = 0.0,
        loc_end: float = 0.0,
        *,
        excerpt: str = "",
        vec: bytes | None = None,
        name: str = "",
        body: str = "",
        original: str = "",
        focus: float = 0.0,
    ) -> int:
        """Insert a content segment with optional vector and deduplicated FTS entry."""
        has_vec = 1 if vec is not None else 0
        norm_name = normalize_search_text(name) if name else ""
        norm_body = normalize_search_text(body) if body else ""
        raw_orig = original or body
        orig_val = raw_orig if raw_orig and raw_orig != norm_body else ""

        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO segments (file, kind, modality, loc, loc_end, excerpt, focus, has_vec, vec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (file_id, kind.value, modality.value, loc, loc_end, excerpt, focus, has_vec, vec),
            )
            segment_id = int(cursor.lastrowid or 0)
            if vec is not None:
                self._vec_dirty_in_tx = True

            if norm_name or norm_body or orig_val:
                cursor.execute(
                    """
                    INSERT INTO fts (rowid, name, body, original)
                    VALUES (?, ?, ?, ?)
                    """,
                    (segment_id, norm_name, norm_body, orig_val),
                )
            return segment_id

    def get_segment(self, segment_id: int) -> tuple[SegmentKind, float, float, str] | None:
        """Retrieve segment metadata (kind, loc, loc_end, excerpt) by ID."""
        with self._read_cursor() as cursor:
            cursor.execute(
                "SELECT kind, loc, loc_end, excerpt FROM segments WHERE id = ?",
                (segment_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return (
                SegmentKind(row[0]),
                float(row[1]),
                float(row[2]),
                str(row[3]),
            )

    def get_segments_by_ids(self, segment_ids: Iterable[int]) -> dict[int, SegmentMeta]:
        """Batch-fetch segment metadata keyed by segment ID."""
        unique_ids = list(dict.fromkeys(segment_ids))
        if not unique_ids:
            return {}
        segments: dict[int, SegmentMeta] = {}
        with self._read_cursor() as cursor:
            for chunk in _chunked(unique_ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT id, kind, loc, loc_end, excerpt, focus FROM segments WHERE id IN ({placeholders})",
                    chunk,
                )
                for row in cursor.fetchall():
                    meta = SegmentMeta(
                        id=int(row[0]),
                        kind=SegmentKind(row[1]),
                        loc=float(row[2]),
                        loc_end=float(row[3]),
                        excerpt=str(row[4]),
                        focus=float(row[5]),
                    )
                    segments[meta.id] = meta
        return segments

    def get_file_segments(self, file_id: int) -> list[tuple[int, SegmentKind, float, float, str, str]]:
        """Retrieve all non-name segments for a file with their text body."""
        with self._read_cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.kind, s.loc, s.loc_end, s.excerpt,
                       coalesce(nullif(fts.original, ''), fts.body, s.excerpt, '')
                FROM segments s
                LEFT JOIN fts ON fts.rowid = s.id
                WHERE s.file = ? AND s.kind != 'name'
                ORDER BY s.loc, s.id
                """,
                (file_id,),
            )
            return [
                (
                    int(row[0]),
                    SegmentKind(row[1]),
                    float(row[2]),
                    float(row[3]),
                    str(row[4]),
                    str(row[5]),
                )
                for row in cursor.fetchall()
            ]

    def find_code_segments(self, file_id: int, phrase: str) -> list[tuple[int, int, str]]:
        """Find code segments matching an FTS phrase within a specific file."""
        match_expr = self._build_fts_match_expr(phrase.replace("\x00", "").strip())
        if not match_expr:
            return []
        with self._read_cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.loc, coalesce(nullif(fts.original, ''), fts.body)
                FROM fts
                JOIN segments s ON s.id = fts.rowid
                WHERE fts MATCH ? AND s.file = ?
                ORDER BY s.loc
                LIMIT 300
                """,
                (match_expr, file_id),
            )
            return [(int(row[0]), int(row[1] or 1), str(row[2] or "")) for row in cursor.fetchall()]

    def get_segment_text(self, segment_id: int) -> tuple[int, str] | None:
        """Retrieve starting line/loc and text content for a segment from FTS or excerpt."""
        with self._read_cursor() as cursor:
            cursor.execute(
                """
                SELECT s.loc, coalesce(nullif(fts.original, ''), nullif(fts.body, ''), s.excerpt)
                FROM segments s
                LEFT JOIN fts ON fts.rowid = s.id
                WHERE s.id = ?
                """,
                (segment_id,),
            )
            row = cursor.fetchone()
            if not row or not row[1]:
                return None
            return int(row[0] or 1), str(row[1])

    def update_file_state(
        self,
        file_id: int,
        state: FileState,
        detail: str = "",
        info: dict[str, object] | None = None,
    ) -> None:
        """Update file processing state, details, and metadata."""
        info_json = json.dumps(info) if info else None
        with self.transaction() as cursor:
            cursor.execute(
                """
                UPDATE files
                SET state = ?, detail = ?, info = ?, indexed_at = ?
                WHERE id = ?
                """,
                (state.value, detail or None, info_json, time.time(), file_id),
            )

    @staticmethod
    def _build_vector_snapshot(rows: list[tuple[Any, ...]]) -> VectorSnapshot:
        expected_bytes = 0
        for row in rows:
            blob = row[3]
            if isinstance(blob, (bytes, memoryview)) and len(blob) > 0 and len(blob) % 2 == 0:
                expected_bytes = len(blob)
                break
        if expected_bytes == 0:
            return _empty_vector_snapshot()

        dim = expected_bytes // 2
        segment_ids: list[int] = []
        file_ids: list[int] = []
        mod_codes: list[int] = []
        scale_flags: list[bool] = []
        blobs: list[bytes] = []
        seen_file_mod: set[tuple[int, int]] = set()

        for row in rows:
            blob = row[3]
            if not isinstance(blob, (bytes, memoryview)) or len(blob) != expected_bytes:
                continue
            seg_id = int(row[0])
            fid = int(row[1])
            mod_code = MODALITY_TO_CODE[Modality(row[2])]
            segment_ids.append(seg_id)
            file_ids.append(fid)
            mod_codes.append(mod_code)
            blobs.append(bytes(blob))
            key = (fid, mod_code)
            if key not in seen_file_mod:
                seen_file_mod.add(key)
                scale_flags.append(True)
            else:
                scale_flags.append(False)

        if not blobs:
            return _empty_vector_snapshot()

        seg_arr = np.asarray(segment_ids, dtype=np.int64)
        file_arr = np.asarray(file_ids, dtype=np.int64)
        mod_arr = np.asarray(mod_codes, dtype=np.uint8)
        scale_arr = np.asarray(scale_flags, dtype=np.bool_)
        matrix = bytes_to_float16(b"".join(blobs)).reshape(len(blobs), dim).astype(np.float32)

        for arr in (seg_arr, file_arr, mod_arr, scale_arr, matrix):
            arr.setflags(write=False)

        return VectorSnapshot(
            segment_ids=seg_arr,
            file_ids=file_arr,
            modality_codes=mod_arr,
            matrix=matrix,
            scale_mask=scale_arr,
        )

    def get_vector_snapshot(self) -> VectorSnapshot:
        """Return a cached, immutable RAM snapshot of all indexed vectors."""
        with self._vec_cache_lock:
            use_cache = not (self._is_memory and self._vec_dirty_in_tx)
            if use_cache and self._vec_cache is not None and self._vec_cache_gen == self._vec_generation:
                return self._vec_cache
            gen_before = self._vec_generation

        with self._read_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, file, modality, vec
                FROM segments
                WHERE has_vec = 1 AND vec IS NOT NULL
                ORDER BY id
                """
            )
            rows = cursor.fetchall()

        snapshot = self._build_vector_snapshot(rows) if rows else _empty_vector_snapshot()
        with self._vec_cache_lock:
            if use_cache and gen_before == self._vec_generation:
                self._vec_cache = snapshot
                self._vec_cache_gen = gen_before
        return snapshot

    def get_all_vectors_with_scale(
        self,
    ) -> tuple[list[int], list[int], list[Modality], npt.NDArray[np.float32], list[bool]]:
        """Return segment_ids, file_ids, modalities, stacked vectors matrix, and sets_scale mask."""
        snap = self.get_vector_snapshot()
        return (
            snap.segment_ids.tolist(),
            snap.file_ids.tolist(),
            [CODE_TO_MODALITY[int(c)] for c in snap.modality_codes],
            snap.matrix,
            snap.scale_mask.tolist(),
        )

    @staticmethod
    def _build_fts_match_expr(stripped: str) -> str:
        if not stripped:
            return ""
        if _PREBUILT_FTS_EXPR_RE.match(stripped):
            return stripped
        all_words = re.findall(r"\w+", stripped)
        words = [w for w in all_words if len(w) >= _MIN_FTS_WORD_LEN] or all_words
        if not words:
            return ""
        return " OR ".join(f'"{w.replace(chr(34), chr(34) * 2)}"*' for w in words)

    @staticmethod
    def _row_to_fts_match(row: tuple[Any, ...], terms: list[str] | None) -> FtsMatchRow:
        body = str(row[3] or "")
        orig = str(row[4] or "")
        raw_snip = str(row[6] or "")
        source_text = orig or body
        snip = extract_snippet(source_text, terms) if terms is not None and source_text else None
        return {
            "segment_id": int(row[0]),
            "file_id": int(row[1]) if row[1] is not None else None,
            "name": str(row[2] or ""),
            "body": body,
            "original": orig,
            "rank": float(row[5]),
            "snippet": snip if snip is not None else raw_snip,
            "loc": float(row[7]) if row[7] is not None else 0.0,
            "excerpt": str(row[8] or ""),
        }

    def fts_search(self, query: str, limit: int = 100, terms: list[str] | None = None) -> list[FtsMatchRow]:
        """Perform full-text search using BM25 ranking with boosted filename weight."""
        match_expr = self._build_fts_match_expr(query.replace("\x00", "").strip())
        if not match_expr:
            return []

        with self._read_cursor() as cursor:
            cursor.execute(
                """
                SELECT fts.rowid, s.file, fts.name, fts.body, fts.original, bm25(fts, 10.0, 1.0) AS rank,
                       coalesce(
                           nullif(snippet(fts, 1, '«', '»', '…', 12), ''),
                           snippet(fts, 0, '«', '»', '…', 12)
                       ) AS snippet,
                       s.loc, s.excerpt
                FROM fts
                LEFT JOIN segments s ON s.id = fts.rowid
                WHERE fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match_expr, limit),
            )
            rows = cursor.fetchall()

        return [self._row_to_fts_match(row, terms) for row in rows]

    def is_indexed(self, pattern: str) -> bool:
        """Check if an FTS pattern matches any indexed document."""
        match_expr = self._build_fts_match_expr(pattern.replace("\x00", "").strip())
        if not match_expr:
            return False
        with self._read_cursor() as cursor:
            cursor.execute("SELECT 1 FROM fts WHERE fts MATCH ? LIMIT 1", (match_expr,))
            return cursor.fetchone() is not None

    def compute_coverage(self, patterns: list[str], file_ids: list[int]) -> dict[int, float]:
        """Calculate query term coverage share for candidate files across all their segments."""
        if not patterns or not file_ids:
            return {}
        unique_fids = list(dict.fromkeys(file_ids))
        coverage: dict[int, float] = dict.fromkeys(unique_fids, 0.0)
        weight = 1.0 / len(patterns)
        with self._read_cursor() as cursor:
            for pattern in patterns:
                match_expr = self._build_fts_match_expr(pattern.replace("\x00", "").strip())
                if not match_expr:
                    continue
                for chunk in _chunked(unique_fids):
                    placeholders = ",".join("?" * len(chunk))
                    cursor.execute(
                        f"""
                        SELECT DISTINCT s.file
                        FROM fts
                        JOIN segments s ON s.id = fts.rowid
                        WHERE fts MATCH ? AND s.file IN ({placeholders})
                        """,
                        [match_expr, *chunk],
                    )
                    for row in cursor.fetchall():
                        coverage[int(row[0])] += weight
        return coverage

    def stats(self) -> dict[str, int]:
        """Return database summary counts using indexed queries."""
        with self._read_cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END), 0)
                FROM files
                """
            )
            files_count, pending_count, failed_count = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM segments")
            segments_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM segments WHERE has_vec = 1")
            vectors_count = cursor.fetchone()[0]
            return {
                "files": int(files_count),
                "pending": int(pending_count),
                "failed": int(failed_count),
                "segments": int(segments_count),
                "vectors": int(vectors_count),
            }

    @staticmethod
    def _row_to_file_record(row: tuple[Any, ...]) -> FileRecord:
        return FileRecord(
            id=int(row[0]),
            path=str(row[1]),
            kind=FileKind(row[2]),
            size=int(row[3]),
            mtime=float(row[4]),
            inode=int(row[5]),
            device=int(row[6]),
            state=FileState(row[7]),
            detail=str(row[8] or ""),
            info=json.loads(row[9]) if row[9] else {},
            indexed_at=float(row[10] or 0.0),
        )
