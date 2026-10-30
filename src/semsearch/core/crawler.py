"""Filesystem crawler and candidate synchronization engine."""

import os
from dataclasses import dataclass
from pathlib import Path

from semsearch.core.database import Database
from semsearch.core.rules import (
    SKIPPED_FOLDER_NAMES,
    classify_file,
    is_dataset_folder,
    is_path_excluded,
    should_skip,
)
from semsearch.core.types import Candidate, FileKind, FileRecord, FileState

_MTIME_TOLERANCE = 0.001


@dataclass
class SyncReport:
    """Reconciliation report from synchronizing candidates against the database."""

    added: int = 0
    changed: int = 0
    moved: int = 0
    deleted: int = 0
    unchanged: int = 0


def _should_recurse_dir(entry: os.DirEntry[str], excluded_folders: list[str] | set[str] | None) -> bool:
    return (
        entry.name not in SKIPPED_FOLDER_NAMES
        and not entry.name.startswith(".")
        and not entry.name.endswith(".noindex")
        and not is_path_excluded(entry.path, excluded_folders)
    )


def _scan_dir_entries(
    current_dir: Path,
    excluded_folders: list[str] | set[str] | None,
) -> tuple[list[Path], list[os.DirEntry[str]]]:
    subdirs: list[Path] = []
    dir_files: list[os.DirEntry[str]] = []
    try:
        with os.scandir(current_dir) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    if _should_recurse_dir(entry, excluded_folders):
                        subdirs.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    dir_files.append(entry)
    except OSError:
        return [], []
    return subdirs, dir_files


def _build_candidate(
    entry: os.DirEntry[str],
    *,
    is_code: bool,
    excluded_folders: list[str] | set[str] | None,
    excluded_types: list[str] | set[str] | None,
) -> Candidate | None:
    skip, _ = should_skip(
        entry.path,
        is_code_mode=is_code,
        excluded_folders=excluded_folders,
        excluded_types=excluded_types,
    )
    kind = classify_file(entry.path, is_code_mode=is_code)
    if skip or kind is None:
        return None
    try:
        stat = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    return Candidate(
        path=str(Path(entry.path).resolve()),
        kind=kind,
        size=stat.st_size,
        mtime=stat.st_mtime,
        inode=stat.st_ino,
        device=stat.st_dev,
    )


def crawl_folder(
    root: str,
    *,
    is_code: bool = False,
    excluded_folders: list[str] | set[str] | None = None,
    excluded_types: list[str] | set[str] | None = None,
) -> list[Candidate]:
    """Recursively scan files under root using os.scandir, skipping ignored folders, secrets, and datasets."""
    root_path = Path(root).resolve()
    if not root_path.exists() or not root_path.is_dir() or is_path_excluded(str(root_path), excluded_folders):
        return []

    candidates: list[Candidate] = []
    dir_stack = [root_path]

    while dir_stack:
        current_dir = dir_stack.pop()
        subdirs, dir_files = _scan_dir_entries(current_dir, excluded_folders)
        dir_stack.extend(subdirs)

        if current_dir != root_path and not is_code and is_dataset_folder([e.path for e in dir_files]):
            dir_files = [
                e
                for e in dir_files
                if classify_file(e.path, is_code_mode=is_code) not in (FileKind.IMAGE, FileKind.AUDIO, FileKind.VIDEO)
            ]

        for entry in dir_files:
            candidate = _build_candidate(
                entry,
                is_code=is_code,
                excluded_folders=excluded_folders,
                excluded_types=excluded_types,
            )
            if candidate is not None:
                candidates.append(candidate)

    return candidates


def _is_under_root(path: str, root: str) -> bool:
    prefix = root if root.endswith("/") else f"{root}/"
    return path == root or path.startswith(prefix)


def _sync_existing_path(db: Database, existing: FileRecord, candidate: Candidate, report: SyncReport) -> None:
    if existing.size != candidate.size or abs(existing.mtime - candidate.mtime) > _MTIME_TOLERANCE:
        db.requeue_file(existing.id, candidate)
        report.changed += 1
    elif existing.kind != candidate.kind:
        if existing.state == FileState.DONE:
            db.update_file_kind(existing.id, candidate.kind)
        else:
            db.requeue_file(existing.id, candidate)
        report.changed += 1
    else:
        report.unchanged += 1


def _sync_existing_inode(
    db: Database,
    existing: FileRecord,
    candidate: Candidate,
    *,
    seen_paths: set[str],
    moved_from: set[str],
    report: SyncReport,
) -> None:
    if existing.path not in seen_paths and existing.path not in moved_from and not Path(existing.path).exists():
        db.move_file(existing.id, candidate.path)
        moved_from.add(existing.path)
        if (
            existing.size != candidate.size
            or abs(existing.mtime - candidate.mtime) > _MTIME_TOLERANCE
            or existing.kind != candidate.kind
        ):
            db.requeue_file(existing.id, candidate)
            report.changed += 1
        else:
            report.moved += 1
    else:
        db.insert_file(candidate)
        report.added += 1


def _should_delete_unseen(
    path: str,
    roots: list[str] | None,
    vanished_roots: set[str],
) -> bool:
    if roots is not None:
        return not any(_is_under_root(path, vr) for vr in vanished_roots)
    return not Path(path).exists()


def sync_candidates(
    db: Database,
    candidates: list[Candidate],
    roots: list[str] | None = None,
) -> SyncReport:
    """Reconcile crawled candidates against database records."""
    report = SyncReport()

    if roots is not None:
        resolved_roots = [str(Path(r).resolve()) for r in roots]
        vanished_roots = {r for r in resolved_roots if not Path(r).exists()}
        existing_by_id = {f.id: f for r in resolved_roots for f in db.find_files_under(r)}
        existing_files = list(existing_by_id.values())
    else:
        vanished_roots = set()
        existing_files = db.find_all_files()

    by_path = {f.path: f for f in existing_files}
    by_inode = {(f.device, f.inode): f for f in existing_files}

    seen_paths = {c.path for c in candidates}
    moved_from: set[str] = set()

    for candidate in candidates:
        if candidate.path in by_path:
            _sync_existing_path(db, by_path[candidate.path], candidate, report)
        elif (candidate.device, candidate.inode) in by_inode:
            _sync_existing_inode(
                db,
                by_inode[(candidate.device, candidate.inode)],
                candidate,
                seen_paths=seen_paths,
                moved_from=moved_from,
                report=report,
            )
        else:
            db.insert_file(candidate)
            report.added += 1

    for existing in existing_files:
        if (
            existing.path not in seen_paths
            and existing.path not in moved_from
            and _should_delete_unseen(existing.path, roots, vanished_roots)
        ):
            db.delete_file(existing.id)
            report.deleted += 1

    return report
