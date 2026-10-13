"""Unit tests for file classification, screenshot prefixes, and skip rules."""

from semsearch.core.rules import classify_file, should_skip
from semsearch.core.types import FileKind


def test_spanish_screenshot_classification():
    """Verify Spanish screenshot filenames classify as FileKind.SCREENSHOT."""
    assert classify_file("/home/user/Pictures/Captura de pantalla_20260927_044528.png") == FileKind.SCREENSHOT
    assert classify_file("/home/user/Pictures/captura_01.jpg") == FileKind.SCREENSHOT


def test_code_mode_markdown_classification():
    """Verify markdown and rst files classify as CODE in code mode and DOC in general mode."""
    assert classify_file("/repo/README.md", is_code_mode=False) == FileKind.DOC
    assert classify_file("/repo/README.md", is_code_mode=True) == FileKind.CODE
    skip, _ = should_skip("/repo/README.md", is_code_mode=True)
    assert skip is False


def test_should_skip_exclusions():
    """Verify should_skip honors excluded_folders and excluded_types."""
    skip, reason = should_skip(
        "/home/user/Documents/notes.txt",
        is_code_mode=False,
        excluded_types=["txt", "heic"],
    )
    assert skip is True
    assert "excluded file type" in reason

    skip_dir, reason_dir = should_skip(
        "/home/user/Archive/old.pdf",
        is_code_mode=False,
        excluded_folders=["/home/user/Archive"],
    )
    assert skip_dir is True
    assert "excluded folder" in reason_dir
