"""SQLite WAL database management and full-text search indexing."""

import json
from pathlib import Path
import re
import sqlite3
import time
import numpy as np

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


class Database:
    """SQLite WAL storage manager for SemSearch files and segments."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init_db()

    @property
    def db_path(self) -> str:
        """Database file path."""
        return self.path

    def _init_db(self) -> None:
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
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_meta(self, key: str) -> str | None:
        """Retrieve metadata value by key."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        """Set or remove metadata key/value."""
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
        file_id = cursor.lastrowid
        file_path = Path(candidate.path)
        name = file_path.name
        folder = " ".join(file_path.parent.parts)

        cursor.execute(
            """
            INSERT INTO segments (file, kind, modality, loc, loc_end, excerpt, vec)
            VALUES (?, ?, ?, 0.0, 0.0, '', NULL)
            """,
            (file_id, SegmentKind.NAME.value, Modality.NONE.value),
        )
        segment_id = cursor.lastrowid
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

    def move_file(self, file_id: int, new_path: str) -> None:
        """Update file path and its FTS name row without mutating segments or vectors."""
        cursor = self._conn.cursor()
        cursor.execute("UPDATE files SET path = ? WHERE id = ?", (new_path, file_id))

        new_path_obj = Path(new_path)
        new_name = new_path_obj.name
        new_folder = " ".join(new_path_obj.parent.parts)

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
        cursor = self._conn.cursor()
        cursor.execute(
            "DELETE FROM fts WHERE rowid IN (SELECT id FROM segments WHERE file = ?)",
            (file_id,),
        )
        cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self._conn.commit()

    def find_by_path(self, path: str) -> FileRecord | None:
        """Find a file record by exact path."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at
            FROM files WHERE path = ?
            """,
            (path,),
        )
        row = cursor.fetchone()
        return self._row_to_file_record(row) if row else None

    def find_by_id(self, file_id: int) -> FileRecord | None:
        """Find a file record by file ID."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at
            FROM files WHERE id = ?
            """,
            (file_id,),
        )
        row = cursor.fetchone()
        return self._row_to_file_record(row) if row else None

    find_file = find_by_id

    def find_by_inode(self, device: int, inode: int) -> FileRecord | None:
        """Find a file record by device and inode."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at
            FROM files WHERE device = ? AND inode = ?
            """,
            (device, inode),
        )
        row = cursor.fetchone()
        return self._row_to_file_record(row) if row else None

    def find_all_files(self) -> list[FileRecord]:
        """Retrieve all file records."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at
            FROM files
            """
        )
        return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def find_files_under(self, root: str) -> list[FileRecord]:
        """Query all files under a specific root prefix."""
        root_path = str(Path(root).resolve())
        pattern = f"{root_path}/%" if root_path != "/" else "/%"
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at
            FROM files
            WHERE path = ? OR path LIKE ?
            """,
            (root_path, pattern),
        )
        return [self._row_to_file_record(row) for row in cursor.fetchall()]

    def list_pending(self, limit: int = 0) -> list[FileRecord]:
        """List pending files prioritized by file kind and modification time."""
        sql = """
            SELECT id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at
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

    def insert_segment(
        self,
        file_id: int,
        kind: SegmentKind,
        modality: Modality,
        loc: float,
        loc_end: float,
        excerpt: str,
        vec: bytes | None,
        name: str,
        body: str,
        original: str,
    ) -> int:
        """Insert a content segment with optional vector and FTS entry."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO segments (file, kind, modality, loc, loc_end, excerpt, vec)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, kind.value, modality.value, loc, loc_end, excerpt, vec),
        )
        segment_id = cursor.lastrowid
        if name or body or original:
            cursor.execute(
                """
                INSERT INTO fts (rowid, name, body, original)
                VALUES (?, ?, ?, ?)
                """,
                (segment_id, name, body, original),
            )
        self._conn.commit()
        return segment_id

    def update_file_state(
        self,
        file_id: int,
        state: FileState,
        detail: str = "",
        info: dict | None = None,
    ) -> None:
        """Update file processing state, details, and metadata."""
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

    def get_all_vectors(self) -> tuple[list[int], list[int], list[Modality], np.ndarray]:
        """Return segment_ids, file_ids, modalities, and stacked vectors matrix."""
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
            return [], [], [], np.empty((0, 768), dtype=np.float32)

        segment_ids = [r[0] for r in rows]
        file_ids = [r[1] for r in rows]
        modalities = [Modality(r[2]) for r in rows]
        matrix = np.vstack([np.frombuffer(r[3], dtype=np.float16).astype(np.float32) for r in rows])
        return segment_ids, file_ids, modalities, matrix

    def fts_search(self, query: str, limit: int = 100) -> list[dict]:
        """Perform full-text search using BM25 ranking."""
        words = re.findall(r"\w+", query)
        if not words:
            return []
        match_expr = " OR ".join(f'"{w}"*' for w in words)
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT fts.rowid, s.file, fts.name, fts.body, fts.original, bm25(fts, 3.0, 1.0) AS rank,
                   snippet(fts, -1, '«', '»', '…', 16) AS snippet
            FROM fts
            LEFT JOIN segments s ON s.id = fts.rowid
            WHERE fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match_expr, limit),
        )
        return [
            {
                "segment_id": row[0],
                "file_id": row[1],
                "name": row[2],
                "body": row[3],
                "original": row[4],
                "rank": row[5],
                "snippet": row[6],
            }
            for row in cursor.fetchall()
        ]

    def stats(self) -> dict[str, int]:
        """Return database summary counts."""
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

    def _row_to_file_record(self, row: tuple) -> FileRecord:
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
