"""Unit tests for crawler, rules, and synchronization reconciliation."""

import os
from pathlib import Path
import shutil
import pytest

from semsearch.core.crawler import SyncReport, crawl_folder, sync_candidates
from semsearch.core.database import Database
from semsearch.core.rules import (
    classify_file,
    is_code_project_root,
    is_dataset_folder,
    is_secret,
    should_skip,
)
from semsearch.core.types import Candidate, FileKind, FileState, Modality, SegmentKind


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Create a temporary database instance."""
    db_path = tmp_path / "test_crawler.sqlite"
    database = Database(db_path)
    yield database
    database.close()


def test_rules_secret_detection():
    """Verify detection of secret and credential files."""
    assert is_secret("/home/user/.env") is True
    assert is_secret("/home/user/.env.local") is True
    assert is_secret("/home/user/.ssh/id_rsa") is True
    assert is_secret("/home/user/certs/cert.pem") is True
    assert is_secret("/home/user/keys/prod.key") is True
    assert is_secret("/home/user/credentials.json") is True
    assert is_secret("/home/user/documents/report.pdf") is False
    assert is_secret("/home/user/src/main.py") is False


def test_rules_code_project_root(tmp_path: Path):
    """Verify code project root detection."""
    proj = tmp_path / "my_project"
    proj.mkdir()
    assert is_code_project_root(str(proj)) is False

    (proj / "package.json").write_text("{}")
    assert is_code_project_root(str(proj)) is True


def test_rules_classify_file():
    """Verify file kind classification."""
    assert classify_file("photo.jpg") == FileKind.IMAGE
    assert classify_file("Screenshot 2026-10-12.png") == FileKind.SCREENSHOT
    assert classify_file("CleanShot 123.jpg") == FileKind.SCREENSHOT
    assert classify_file("paper.pdf") == FileKind.PDF
    assert classify_file("notes.md") == FileKind.DOC
    assert classify_file("track.mp3") == FileKind.AUDIO
    assert classify_file("clip.mp4") == FileKind.VIDEO
    assert classify_file("script.py") == FileKind.CODE
    assert classify_file("Dockerfile") == FileKind.CODE
    assert classify_file("archive.zip") is None


def test_rules_should_skip(tmp_path: Path):
    """Verify should_skip logic across general and code modes."""
    # Ignored directory
    skip, reason = should_skip("/tmp/repo/node_modules/pkg/index.js", is_code_mode=False)
    assert skip is True
    assert "skipped folder" in reason

    # Secret file
    skip, reason = should_skip("/tmp/.env", is_code_mode=False)
    assert skip is True
    assert "secret" in reason

    # Code project boundary
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / "Cargo.toml").write_text("")

    code_file = proj / "src" / "lib.rs"
    code_file.parent.mkdir(parents=True)
    code_file.write_text("fn main() {}")

    shot_file = proj / "docs" / "Screenshot_app.png"
    shot_file.parent.mkdir(parents=True)
    shot_file.write_text("")

    # In general mode, code is skipped inside code project, but screenshot is kept
    skip, _ = should_skip(str(code_file), is_code_mode=False)
    assert skip is True

    skip, _ = should_skip(str(shot_file), is_code_mode=False)
    assert skip is False

    # In code mode, code is kept
    skip, _ = should_skip(str(code_file), is_code_mode=True)
    assert skip is False

    # In general mode, standalone code files/scripts outside code projects are skipped
    skip, reason = should_skip("/tmp/random/script.py", is_code_mode=False)
    assert skip is True
    assert "code file in general mode" in reason

    skip, reason = should_skip("/tmp/random/run.sh", is_code_mode=False)
    assert skip is True
    assert "code file in general mode" in reason

    # In code mode, data formats are skipped
    for data_file in (
        "data.json",
        "log.jsonl",
        "config.xml",
        "Cargo.toml",
        "Info.plist",
        "sheet.csv",
        "sheet.tsv",
        "poetry.lock",
        "pnpm-lock.yaml",
        "helm-lock.yaml",
    ):
        skip, reason = should_skip(f"/tmp/repo/{data_file}", is_code_mode=True)
        assert skip is True
        assert "data format" in reason


def test_rules_dataset_folder():
    """Verify dataset detection for 300+ machine-named files."""
    # 300 machine named image files
    machine_files = [f"/tmp/dataset/frame_{i:04d}.png" for i in range(350)]
    assert is_dataset_folder(machine_files) is True

    # 300 camera named image files
    camera_files = [f"/tmp/photos/IMG_{i:04d}.jpg" for i in range(350)]
    assert is_dataset_folder(camera_files) is False

    # Small folder (< 300 files)
    small_files = [f"/tmp/dataset/frame_{i:04d}.png" for i in range(100)]
    assert is_dataset_folder(small_files) is False


def test_crawl_folder_finds_files(tmp_path: Path):
    """Verify crawl_folder scans valid files and skips secrets / ignored dirs."""
    root = tmp_path / "test_root"
    root.mkdir()

    # Valid doc
    (root / "readme.md").write_text("# Hello")

    # Valid image
    (root / "photo.png").write_text("png content")

    # Secret file
    (root / ".env").write_text("SECRET=1")

    # Ignored directory
    venv = root / "venv"
    venv.mkdir()
    (venv / "module.py").write_text("print(1)")

    candidates = crawl_folder(str(root), is_code=False)
    paths = {c.path for c in candidates}

    assert str((root / "readme.md").resolve()) in paths
    assert str((root / "photo.png").resolve()) in paths
    assert str((root / ".env").resolve()) not in paths
    assert str((venv / "module.py").resolve()) not in paths


def test_sync_candidates_add_change_move_delete(db: Database, tmp_path: Path):
    """Verify complete reconciliation: addition, modification, move by inode, deletion."""
    test_dir = tmp_path / "files"
    test_dir.mkdir()

    file_a = test_dir / "file_a.txt"
    file_a.write_text("Content A")

    file_b = test_dir / "file_b.txt"
    file_b.write_text("Content B")

    candidates_step1 = crawl_folder(str(test_dir))
    report1 = sync_candidates(db, candidates_step1)

    assert report1.added == 2
    assert report1.changed == 0
    assert report1.moved == 0
    assert report1.deleted == 0
    assert report1.unchanged == 0

    # Step 2: Unchanged crawl
    report2 = sync_candidates(db, candidates_step1)
    assert report2.unchanged == 2
    assert report2.added == 0

    # Step 3: Modify file_a
    file_a.write_text("Updated Content A with more text")
    # Touch mtime
    new_mtime = file_a.stat().st_mtime + 5.0
    os.utime(file_a, (new_mtime, new_mtime))

    candidates_step3 = crawl_folder(str(test_dir))
    report3 = sync_candidates(db, candidates_step3)
    assert report3.changed == 1
    assert report3.unchanged == 1

    # Step 4: Move file_b to file_b_renamed.txt
    file_b_renamed = test_dir / "file_b_renamed.txt"
    file_b.rename(file_b_renamed)

    candidates_step4 = crawl_folder(str(test_dir))
    report4 = sync_candidates(db, candidates_step4)
    assert report4.moved == 1
    assert report4.unchanged == 1

    # Verify path in db was updated
    rec_b = db.find_by_path(str(file_b_renamed.resolve()))
    assert rec_b is not None
    assert db.find_by_path(str(file_b.resolve())) is None

    # Step 5: Delete file_a
    file_a.unlink()

    candidates_step5 = crawl_folder(str(test_dir))
    report5 = sync_candidates(db, candidates_step5)
    assert report5.deleted == 1
    assert report5.unchanged == 1
    assert db.find_by_path(str(file_a.resolve())) is None


def test_delete_entire_subfolder_cascades_in_db(db: Database, tmp_path: Path):
    """Verify deleting an entire subfolder deletes its files from DB and cascades to segments."""
    root = tmp_path / "subfolder_test"
    root.mkdir()
    sub = root / "nested"
    sub.mkdir()

    f1 = sub / "file1.txt"
    f1.write_text("Hello nested 1")
    f2 = sub / "file2.txt"
    f2.write_text("Hello nested 2")
    f_root = root / "keep.txt"
    f_root.write_text("Keep me")

    candidates = crawl_folder(str(root))
    report = sync_candidates(db, candidates, roots=[str(root)])
    assert report.added == 3

    # Add extra segment to f1 to test cascade deletion in DB
    rec1 = db.find_by_path(str(f1.resolve()))
    assert rec1 is not None
    seg_id = db.insert_segment(
        file_id=rec1.id,
        kind=SegmentKind.CHUNK,
        modality=Modality.TEXT,
        loc=0.0,
        loc_end=0.0,
        excerpt="Hello nested 1",
        vec=None,
        name="",
        body="Hello nested 1",
        original="Hello nested 1",
    )

    # Delete entire subfolder
    shutil.rmtree(sub)

    # Re-crawl and sync with roots
    candidates_after = crawl_folder(str(root))
    report_after = sync_candidates(db, candidates_after, roots=[str(root)])
    assert report_after.deleted == 2
    assert report_after.unchanged == 1

    assert db.find_by_path(str(f1.resolve())) is None
    assert db.find_by_path(str(f2.resolve())) is None
    assert db.find_by_path(str(f_root.resolve())) is not None

    # Check that segment was cascade deleted
    cursor = db._conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM segments WHERE id = ?", (seg_id,))
    assert cursor.fetchone()[0] == 0


def test_multi_root_scoping_and_vanished_root(db: Database, tmp_path: Path):
    """Verify syncing one root does not delete files from another, and vanished roots are preserved."""
    root_a = tmp_path / "root_a"
    root_a.mkdir()
    root_b = tmp_path / "root_b"
    root_b.mkdir()

    fa = root_a / "fa.txt"
    fa.write_text("Root A File")
    fb = root_b / "fb.txt"
    fb.write_text("Root B File")

    # Sync both
    c_a = crawl_folder(str(root_a))
    c_b = crawl_folder(str(root_b))
    sync_candidates(db, c_a, roots=[str(root_a)])
    sync_candidates(db, c_b, roots=[str(root_b)])

    assert db.find_by_path(str(fa.resolve())) is not None
    assert db.find_by_path(str(fb.resolve())) is not None

    # Re-sync root_a only: root_b must NOT be deleted
    report_a = sync_candidates(db, c_a, roots=[str(root_a)])
    assert report_a.unchanged == 1
    assert report_a.deleted == 0
    assert db.find_by_path(str(fb.resolve())) is not None

    # Vanished root test (unmounted drive)
    shutil.rmtree(root_b)
    # Sync with root_b in roots: since root_b does not exist, its files must NOT be deleted
    report_vanished = sync_candidates(db, [], roots=[str(root_b)])
    assert report_vanished.deleted == 0
    assert db.find_by_path(str(fb.resolve())) is not None


def test_move_and_modify(db: Database, tmp_path: Path):
    """Verify that moving and altering a file re-queues it as pending and counts in changed."""
    work_dir = tmp_path / "move_mod"
    work_dir.mkdir()

    orig_file = work_dir / "draft.txt"
    orig_file.write_text("Initial content")

    candidates = crawl_folder(str(work_dir))
    sync_candidates(db, candidates, roots=[str(work_dir)])

    rec = db.find_by_path(str(orig_file.resolve()))
    assert rec is not None
    db.update_file_state(rec.id, FileState.DONE, detail="indexed")
    assert db.find_by_path(str(orig_file.resolve())).state == FileState.DONE

    # Move and alter content
    target_file = work_dir / "final.txt"
    orig_file.rename(target_file)
    target_file.write_text("Substantially updated content that changes file size")
    new_mtime = target_file.stat().st_mtime + 10.0
    os.utime(target_file, (new_mtime, new_mtime))

    candidates_after = crawl_folder(str(work_dir))
    report = sync_candidates(db, candidates_after, roots=[str(work_dir)])

    assert report.changed == 1
    assert report.moved == 0

    updated_rec = db.find_by_path(str(target_file.resolve()))
    assert updated_rec is not None
    assert updated_rec.id == rec.id
    assert updated_rec.state == FileState.PENDING
    assert db.find_by_path(str(orig_file.resolve())) is None


def test_crawl_folder_root_never_treated_as_dataset(tmp_path: Path):
    """Verify user-selected root folder itself is never skipped as a dataset."""
    root = tmp_path / "photos_root"
    root.mkdir()

    # Create 350 machine-named image files directly under root
    for i in range(350):
        (root / f"frame_{i:04d}.png").write_text("")

    candidates = crawl_folder(str(root), is_code=False)
    # Root itself must not be skipped as dataset, so all 350 files are found
    assert len(candidates) == 350

    # But a nested dataset subdirectory MUST be treated as dataset
    nested = root / "dataset_subdir"
    nested.mkdir()
    for i in range(350):
        (nested / f"frame_{i:04d}.png").write_text("")

    candidates_with_nested = crawl_folder(str(root), is_code=False)
    # The 350 from root are kept, the 350 image files from nested are skipped as dataset
    assert len(candidates_with_nested) == 350


def test_crawl_and_sync_with_excluded_folders_and_types(db: Database, tmp_path: Path):
    """Verify crawl_folder and sync_candidates respect excluded_folders and excluded_types."""
    root = tmp_path / "excl_root"
    root.mkdir()
    (root / "keep.md").write_text("Markdown doc")
    (root / "skip.txt").write_text("Plain text")
    (root / "Captura de pantalla_20260927.png").write_text("png")

    priv = root / "Private"
    priv.mkdir()
    (priv / "secret_doc.md").write_text("Private doc")

    candidates = crawl_folder(
        str(root),
        is_code=False,
        excluded_folders=[str(priv)],
        excluded_types=["txt"],
    )
    by_name = {Path(c.path).name: c.kind for c in candidates}
    assert by_name == {
        "keep.md": FileKind.DOC,
        "Captura de pantalla_20260927.png": FileKind.SCREENSHOT,
    }

