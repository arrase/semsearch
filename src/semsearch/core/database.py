"""SQLite WAL database management and full-text search indexing."""

import json
import re
import sqlite3
import threading
import time
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

SCHEMA_VERSION = 5
MAIN_DB_FILE = "index.sqlite"
CODE_DB_FILE = "code.sqlite"
FILE_COLUMNS = "id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at"
_MIN_FTS_WORD_LEN = 2


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


def _folder_text(path_obj: Path) -> str:
    home = str(Path.home())
    parent_str = str(path_obj.parent).removeprefix(home)
    parts = [p for p in parent_str.split("/") if p]
    return " ".join(parts)


class Database:
    """SQLite WAL storage manager for SemSearch files and segments."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.execute("PRAGMA busy_timeout = 5000;")
            cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")

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
            cursor.execute("CREATE INDEX IF NOT EXISTS files_inode ON files(device, inode);")
            cursor.execute("CREATE INDEX IF NOT EXISTS files_state ON files(state, kind);")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY,
                    file INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    modality TEXT NOT NULL,
                    loc REAL,
                    loc_end REAL,
                    excerpt TEXT,
                    vec BLOB
                );
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS segments_file ON segments(file);")

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
            self._conn.commit()

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            self._conn.close()

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
        with self._lock:
            self._conn.execute("VACUUM")

    def shrink_memory(self) -> None:
        """Release non-essential SQLite heap and page cache allocations."""
        with self._lock:
            self._conn.execute("PRAGMA shrink_memory;")

    def get_meta(self, key: str) -> str | None:
        """Retrieve metadata value by key."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        """Set or remove metadata key/value."""
        with self._lock:
            cursor = self._conn.cursor()
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
            self._conn.commit()

    def insert_file(self, candidate: Candidate) -> int:
        """Insert a discovered file candidate as pending and create its name segment and FTS entry."""
        with self._lock:
            cursor = self._conn.cursor()
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
            file_path = Path(candidate.path)
            name = normalize_search_text(file_path.name)
            folder = normalize_search_text(_folder_text(file_path))

            cursor.execute(
                """
                INSERT INTO segments (file, kind, modality, loc, loc_end, excerpt, vec)
                VALUES (?, ?, ?, 0.0, 0.0, '', NULL)
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
            self._conn.commit()
            return file_id

    def requeue_file(self, file_id: int, candidate: Candidate) -> None:
        """Requeue a changed file, clearing extracted segments except the name segment."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM fts WHERE rowid IN (SELECT id FROM segments WHERE file = ? AND kind != 'name')",
                (file_id,),
            )
            cursor.execute(
                "DELETE FROM segments WHERE file = ? AND kind != 'name'",
                (file_id,),
            )
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
            self._conn.commit()

    def update_file_kind(self, file_id: int, kind: FileKind) -> None:
        """Update the classified FileKind of an existing file record."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("UPDATE files SET kind = ? WHERE id = ?", (kind.value, file_id))
            self._conn.commit()

    def move_file(self, file_id: int, new_path: str) -> None:
        """Update file path and its FTS name row without mutating segments or vectors."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("UPDATE files SET path = ? WHERE id = ?", (new_path, file_id))

            new_path_obj = Path(new_path)
            new_name = normalize_search_text(new_path_obj.name)
            new_folder = normalize_search_text(_folder_text(new_path_obj))

            cursor.execute(
                """
                UPDATE fts
                SET name = ?, body = ?
                WHERE rowid IN (SELECT id FROM segments WHERE file = ? AND kind = 'name')
                """,
                (new_name, new_folder, file_id),
            )
            self._conn.commit()

    def delete_file(self, file_id: int) -> None:
        """Delete file record with cascading deletion to segments and FTS."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM fts WHERE rowid IN (SELECT id FROM segments WHERE file = ?)",
                (file_id,),
            )
            cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
            self._conn.commit()

    def find_by_path(self, path: str) -> FileRecord | None:
        """Find a file record by exact path."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE path = ?", (path,))
            row = cursor.fetchone()
            return self._row_to_file_record(row) if row else None

    def find_by_id(self, file_id: int) -> FileRecord | None:
        """Find a file record by file ID."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE id = ?", (file_id,))
            row = cursor.fetchone()
            return self._row_to_file_record(row) if row else None

    def find_by_inode(self, device: int, inode: int) -> FileRecord | None:
        """Find a file record by device and inode."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                f"SELECT {FILE_COLUMNS} FROM files WHERE device = ? AND inode = ?",
                (device, inode),
            )
            row = cursor.fetchone()
            return self._row_to_file_record(row) if row else None

    def find_all_files(self) -> list[FileRecord]:
        """Retrieve all file records."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(f"SELECT {FILE_COLUMNS} FROM files")
            return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def find_files_under(self, root: str) -> list[FileRecord]:
        """Query all files under a specific root prefix."""
        with self._lock:
            root_path = str(Path(root).resolve())
            escaped_root = root_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"{escaped_root}/%" if escaped_root != "/" else "/%"
            cursor = self._conn.cursor()
            cursor.execute(
                f"SELECT {FILE_COLUMNS} FROM files WHERE path = ? OR path LIKE ? ESCAPE '\\'",
                (root_path, pattern),
            )
            return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def list_pending(self, limit: int = 0) -> list[FileRecord]:
        """List pending files prioritized by file kind and modification time."""
        with self._lock:
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
            if limit > 0:
                sql += f" LIMIT {limit}"
            cursor = self._conn.cursor()
            cursor.execute(sql)
            return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def find_partial(self) -> FileRecord | None:
        """Find the most recently modified partially indexed file."""
        with self._lock:
            cursor = self._conn.cursor()
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
    ) -> int:
        """Insert a content segment with optional vector and FTS entry."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO segments (file, kind, modality, loc, loc_end, excerpt, vec)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (file_id, kind.value, modality.value, loc, loc_end, excerpt, vec),
            )
            segment_id = int(cursor.lastrowid or 0)
            norm_name = normalize_search_text(name) if name else ""
            norm_body = normalize_search_text(body) if body else ""
            orig_val = original or (body if norm_body != body else "")

            if norm_name or norm_body or orig_val:
                cursor.execute(
                    """
                    INSERT INTO fts (rowid, name, body, original)
                    VALUES (?, ?, ?, ?)
                    """,
                    (segment_id, norm_name, norm_body, orig_val),
                )
            self._conn.commit()
            return segment_id

    def get_segment(self, segment_id: int) -> tuple[SegmentKind, float, float, str] | None:
        """Retrieve segment metadata (kind, loc, loc_end, excerpt) by ID."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT kind, loc, loc_end, excerpt FROM segments WHERE id = ?",
                (segment_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return (
                SegmentKind(row[0]),
                row[1] if row[1] is not None else 0.0,
                row[2] if row[2] is not None else 0.0,
                row[3] or "",
            )

    def get_file_segments(self, file_id: int) -> list[tuple[int, SegmentKind, float, float, str, str]]:
        """Retrieve all non-name segments for a file with their text body."""
        with self._lock:
            cursor = self._conn.cursor()
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
                    row[0],
                    SegmentKind(row[1]),
                    float(row[2] or 0.0),
                    float(row[3] or 0.0),
                    row[4] or "",
                    row[5] or "",
                )
                for row in cursor.fetchall()
            ]

    def find_code_segments(self, file_id: int, phrase: str) -> list[tuple[int, int, str]]:
        """Find code segments matching an FTS phrase within a specific file."""
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT s.id, s.loc, coalesce(nullif(fts.original, ''), fts.body)
                    FROM fts
                    JOIN segments s ON s.id = fts.rowid
                    WHERE fts MATCH ? AND s.file = ?
                    ORDER BY s.loc
                    LIMIT 300
                    """,
                    (phrase, file_id),
                )
                return [(row[0], int(row[1] or 1), row[2] or "") for row in cursor.fetchall()]
            except sqlite3.Error:
                return []

    def get_segment_text(self, segment_id: int) -> tuple[int, str] | None:
        """Retrieve starting line/loc and text content for a segment from FTS or excerpt."""
        with self._lock:
            cursor = self._conn.cursor()
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
            return int(row[0] or 1), row[1]

    def update_file_state(
        self,
        file_id: int,
        state: FileState,
        detail: str = "",
        info: dict[str, object] | None = None,
    ) -> None:
        """Update file processing state, details, and metadata."""
        with self._lock:
            info_json = json.dumps(info) if info else None
            cursor = self._conn.cursor()
            cursor.execute(
                """
                UPDATE files
                SET state = ?, detail = ?, info = ?, indexed_at = ?
                WHERE id = ?
                """,
                (state.value, detail or None, info_json, time.time(), file_id),
            )
            self._conn.commit()

    def get_all_vectors(self) -> tuple[list[int], list[int], list[Modality], npt.NDArray[np.float32]]:
        """Return segment_ids, file_ids, modalities, and stacked vectors matrix."""
        seg_ids, file_ids, modalities, matrix, _ = self.get_all_vectors_with_scale()
        return seg_ids, file_ids, modalities, matrix

    def get_all_vectors_with_scale(
        self,
    ) -> tuple[list[int], list[int], list[Modality], npt.NDArray[np.float32], list[bool]]:
        """Return segment_ids, file_ids, modalities, stacked vectors matrix, and sets_scale mask."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, file, modality, vec
                FROM segments
                WHERE vec IS NOT NULL
                ORDER BY id
                """
            )
            rows = cursor.fetchall()
            if not rows:
                return [], [], [], np.empty((0, 768), dtype=np.float32), []

            segment_ids: list[int] = []
            file_ids: list[int] = []
            modalities: list[Modality] = []
            sets_scale: list[bool] = []
            seen_file_mod: set[tuple[int, str]] = set()

            for row in rows:
                seg_id, fid, mod_str = row[0], row[1], row[2]
                segment_ids.append(seg_id)
                file_ids.append(fid)
                modalities.append(Modality(mod_str))
                key = (fid, mod_str)
                if key not in seen_file_mod:
                    seen_file_mod.add(key)
                    sets_scale.append(True)
                else:
                    sets_scale.append(False)

            matrix = np.vstack([np.frombuffer(r[3], dtype=np.float16).astype(np.float32) for r in rows])
            return segment_ids, file_ids, modalities, matrix, sets_scale

    @staticmethod
    def _build_fts_match_expr(stripped: str) -> str:
        if '"' in stripped or "*" in stripped or " OR " in stripped:
            return stripped
        words = [w for w in re.findall(r"\w+", stripped) if len(w) >= _MIN_FTS_WORD_LEN] or [stripped]
        return " OR ".join(f'"{w}"*' for w in words)

    def fts_search(self, query: str, limit: int = 100, terms: list[str] | None = None) -> list[FtsMatchRow]:
        """Perform full-text search using BM25 ranking with boosted filename weight."""
        stripped = query.strip()
        if not stripped:
            return []
        match_expr = self._build_fts_match_expr(stripped)

        with self._lock:
            cursor = self._conn.cursor()
            try:
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
            except sqlite3.Error:
                return []

        results: list[FtsMatchRow] = []
        for row in rows:
            orig = row[4]
            raw_snip = row[6] or ""
            snip = extract_snippet(orig, terms) if orig and terms else None
            results.append(
                {
                    "segment_id": int(row[0]),
                    "file_id": int(row[1]) if row[1] is not None else None,
                    "name": str(row[2] or ""),
                    "body": str(row[3] or ""),
                    "original": str(orig or ""),
                    "rank": float(row[5]),
                    "snippet": str(snip or raw_snip),
                    "loc": float(row[7]) if row[7] is not None else 0.0,
                    "excerpt": str(row[8] or ""),
                }
            )
        return results

    def is_indexed(self, pattern: str) -> bool:
        """Check if an FTS pattern matches any indexed document."""
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute("SELECT 1 FROM fts WHERE fts MATCH ? LIMIT 1", (pattern,))
                return cursor.fetchone() is not None
            except sqlite3.Error:
                return False

    def compute_coverage(self, patterns: list[str], file_ids: list[int]) -> dict[int, float]:
        """Calculate query term coverage share for candidate files across all their segments."""
        if not patterns or not file_ids:
            return {}
        coverage: dict[int, float] = dict.fromkeys(file_ids, 0.0)
        files_str = ",".join(str(int(fid)) for fid in file_ids)
        weight = 1.0 / len(patterns)
        with self._lock:
            cursor = self._conn.cursor()
            for pattern in patterns:
                try:
                    cursor.execute(
                        f"""
                        SELECT DISTINCT s.file
                        FROM fts
                        JOIN segments s ON s.id = fts.rowid
                        WHERE fts MATCH ? AND s.file IN ({files_str})
                        """,
                        (pattern,),
                    )
                    for row in cursor.fetchall():
                        coverage[row[0]] += weight
                except sqlite3.Error:
                    continue
        return coverage

    def stats(self) -> dict[str, int]:
        """Return database summary counts."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM files")
            files_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM files WHERE state = 'pending'")
            pending_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM segments")
            segments_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM segments WHERE vec IS NOT NULL")
            vectors_count = cursor.fetchone()[0]
            return {
                "files": files_count,
                "pending": pending_count,
                "segments": segments_count,
                "vectors": vectors_count,
            }

    def _row_to_file_record(self, row: tuple[Any, ...]) -> FileRecord:
        return FileRecord(
            id=row[0],
            path=row[1],
            kind=FileKind(row[2]),
            size=row[3],
            mtime=row[4],
            inode=row[5],
            device=row[6],
            state=FileState(row[7]),
            detail=row[8] or "",
            info=json.loads(row[9]) if row[9] else {},
            indexed_at=row[10] or 0.0,
        )
