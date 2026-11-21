"""Filesystem crawler with top-down project/gitignore tracking and transactional sync."""

import fnmatch
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from semsearch.core.database import Database
from semsearch.core.rules import (
    MAX_TEXT_FILE_BYTES,
    PROJECT_BUNDLE_EXTENSIONS,
    PROJECT_MARKERS,
    SKIPPED_FOLDER_NAMES,
    classify_file,
    is_dataset_folder,
    is_path_excluded,
    normalize_excluded_folders,
    normalize_excluded_types,
    should_skip,
)
from semsearch.core.types import Candidate, FileKind, FileRecord, FileState

_MTIME_TOLERANCE = 0.001
_DATASET_MEDIA_KINDS = frozenset({FileKind.IMAGE, FileKind.AUDIO, FileKind.VIDEO})


@dataclass
class SyncReport:
    """Reconciliation report from synchronizing candidates against the database."""

    added: int = 0
    changed: int = 0
    moved: int = 0
    deleted: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class _GitIgnoreRule:
    base_dir: Path
    pattern: str
    negated: bool
    dir_only: bool
    anchored: bool


def _load_gitignore(dir_path: Path) -> tuple[_GitIgnoreRule, ...]:
    gitignore_path = dir_path / ".gitignore"
    try:
        content = gitignore_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ()

    rules: list[_GitIgnoreRule] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        dir_only = line.endswith("/")
        if dir_only:
            line = line.rstrip("/")
        line = line.removeprefix("**/")
        anchored = "/" in line
        pattern = line.lstrip("/")
        if not pattern:
            continue
        rules.append(
            _GitIgnoreRule(
                base_dir=dir_path,
                pattern=pattern,
                negated=negated,
                dir_only=dir_only,
                anchored=anchored,
            )
        )
    return tuple(rules)


def _is_gitignored(entry_path: Path, *, is_dir: bool, rules: tuple[_GitIgnoreRule, ...]) -> bool:
    if not rules:
        return False
    ignored = False
    name = entry_path.name
    for rule in rules:
        if rule.dir_only and not is_dir:
            continue
        if rule.anchored:
            rel = entry_path.relative_to(rule.base_dir).as_posix()
            matched = fnmatch.fnmatchcase(rel, rule.pattern)
        else:
            matched = fnmatch.fnmatchcase(name, rule.pattern)
        if matched:
            ignored = not rule.negated
    return ignored


def _should_recurse_dir(entry: os.DirEntry[str], excluded_folders: frozenset[str]) -> bool:
    return (
        entry.name not in SKIPPED_FOLDER_NAMES
        and not entry.name.startswith(".")
        and not entry.name.endswith(".noindex")
        and not is_path_excluded(entry.path, excluded_folders)
    )


def _is_project_marker_name(name: str) -> bool:
    return name in PROJECT_MARKERS or Path(name).suffix in PROJECT_BUNDLE_EXTENSIONS


def _scan_dir_entries(
    current_dir: Path,
    excluded_folders: frozenset[str],
) -> tuple[list[os.DirEntry[str]], list[os.DirEntry[str]], bool, bool]:
    subdirs: list[os.DirEntry[str]] = []
    dir_files: list[os.DirEntry[str]] = []
    has_project_marker = False
    has_gitignore = False
    try:
        with os.scandir(current_dir) as it:
            for entry in it:
                if not has_project_marker and _is_project_marker_name(entry.name):
                    has_project_marker = True
                if entry.is_dir(follow_symlinks=False):
                    if _should_recurse_dir(entry, excluded_folders):
                        subdirs.append(entry)
                elif entry.is_file(follow_symlinks=False):
                    if entry.name == ".gitignore":
                        has_gitignore = True
                    dir_files.append(entry)
    except OSError:
        return [], [], False, False
    return subdirs, dir_files, has_project_marker, has_gitignore


def _build_candidate(
    entry: os.DirEntry[str],
    *,
    is_code: bool,
    in_code_project: bool,
    excluded_folders: frozenset[str],
    excluded_types: frozenset[str],
) -> Candidate | None:
    skip, _ = should_skip(
        entry.path,
        is_code_mode=is_code,
        excluded_folders=excluded_folders,
        excluded_types=excluded_types,
        in_code_project=in_code_project,
    )
    if skip:
        return None
    kind = classify_file(entry.path, is_code_mode=is_code)
    if kind is None:
        return None
    try:
        stat = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    if kind in (FileKind.DOC, FileKind.CODE) and stat.st_size > MAX_TEXT_FILE_BYTES:
        return None
    return Candidate(
        path=str(Path(entry.path).resolve()),
        kind=kind,
        size=stat.st_size,
        mtime=stat.st_mtime,
        inode=stat.st_ino,
        device=stat.st_dev,
    )


def _collect_dir_candidates(
    dir_files: list[os.DirEntry[str]],
    *,
    is_root: bool,
    is_code: bool,
    in_code_project: bool,
    rules: tuple[_GitIgnoreRule, ...],
    excluded_folders: frozenset[str],
    excluded_types: frozenset[str],
) -> list[Candidate]:
    filtered = [entry for entry in dir_files if not _is_gitignored(Path(entry.path), is_dir=False, rules=rules)]
    if not is_root and not is_code and is_dataset_folder([entry.path for entry in filtered]):
        filtered = [
            entry for entry in filtered if classify_file(entry.path, is_code_mode=is_code) not in _DATASET_MEDIA_KINDS
        ]

    found: list[Candidate] = []
    for entry in filtered:
        candidate = _build_candidate(
            entry,
            is_code=is_code,
            in_code_project=in_code_project,
            excluded_folders=excluded_folders,
            excluded_types=excluded_types,
        )
        if candidate is not None:
            found.append(candidate)
    return found


def crawl_folder(
    root: str,
    *,
    is_code: bool = False,
    excluded_folders: Iterable[str] | None = None,
    excluded_types: Iterable[str] | None = None,
) -> list[Candidate]:
    """Recursively scan files under root using os.scandir with top-down project and .gitignore filtering."""
    norm_folders = normalize_excluded_folders(excluded_folders)
    norm_types = normalize_excluded_types(excluded_types)
    root_path = Path(root).resolve()
    if not root_path.exists() or not root_path.is_dir() or is_path_excluded(str(root_path), norm_folders):
        return []

    candidates: list[Candidate] = []
    dir_stack: list[tuple[Path, bool, tuple[_GitIgnoreRule, ...]]] = [(root_path, False, ())]

    while dir_stack:
        current_dir, in_code_project, gitignore_rules = dir_stack.pop()
        subdirs, dir_files, has_project_marker, has_gitignore = _scan_dir_entries(current_dir, norm_folders)

        is_root = current_dir == root_path
        dir_in_project = in_code_project or (not is_root and has_project_marker)
        active_rules = gitignore_rules
        if has_gitignore and (is_code or dir_in_project):
            active_rules = gitignore_rules + _load_gitignore(current_dir)

        subdir_paths = (Path(subdir.path) for subdir in subdirs)
        dir_stack.extend(
            (subdir_path, dir_in_project, active_rules)
            for subdir_path in subdir_paths
            if not _is_gitignored(subdir_path, is_dir=True, rules=active_rules)
        )
        candidates.extend(
            _collect_dir_candidates(
                dir_files,
                is_root=is_root,
                is_code=is_code,
                in_code_project=dir_in_project,
                rules=active_rules,
                excluded_folders=norm_folders,
                excluded_types=norm_types,
            )
        )

    return candidates


def _is_under_root(path: str, root: str) -> bool:
    return Path(path).is_relative_to(Path(root))


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
    """Reconcile crawled candidates against database records in a single atomic transaction."""
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

    with db.transaction():
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


def purge_outside_roots(db: Database, active_roots: list[str]) -> int:
    """Delete database file records that fall outside all configured active root folders."""
    resolved_roots = [Path(root).resolve() for root in active_roots]
    orphan_ids = [
        record.id
        for record in db.find_all_files()
        if not any(Path(record.path).resolve().is_relative_to(root) for root in resolved_roots)
    ]
    if not orphan_ids:
        return 0
    with db.transaction():
        for file_id in orphan_ids:
            db.delete_file(file_id)
    return len(orphan_ids)
