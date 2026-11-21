"""Unit tests for file classification, secret detection, minified assets, and skip rules."""

from semsearch.core.rules import (
    DOC_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MAX_TEXT_FILE_BYTES,
    classify_file,
    is_minified_file,
    is_path_excluded,
    is_secret,
    should_skip,
)
from semsearch.core.types import FileKind


def test_spanish_screenshot_classification() -> None:
    """Verify Spanish screenshot filenames classify as FileKind.SCREENSHOT."""
    assert classify_file("/home/user/Pictures/Captura de pantalla_20260927_044528.png") == FileKind.SCREENSHOT
    assert classify_file("/home/user/Pictures/captura_01.jpg") == FileKind.SCREENSHOT


def test_doc_and_svg_extensions() -> None:
    """Verify binary .doc is removed while modern doc formats and .svg remain supported."""
    assert "doc" not in DOC_EXTENSIONS
    assert classify_file("/home/user/legacy.doc") is None
    assert classify_file("/home/user/report.docx") == FileKind.DOC
    assert classify_file("/home/user/notes.odt") == FileKind.DOC
    assert classify_file("/home/user/letter.rtf") == FileKind.DOC
    assert classify_file("/home/user/page.html") == FileKind.DOC
    assert "svg" in IMAGE_EXTENSIONS
    assert classify_file("/home/user/diagram.svg") == FileKind.IMAGE


def test_code_mode_markdown_classification() -> None:
    """Verify markdown and rst files classify as CODE in code mode and DOC in general mode."""
    assert classify_file("/repo/README.md", is_code_mode=False) == FileKind.DOC
    assert classify_file("/repo/README.md", is_code_mode=True) == FileKind.CODE
    skip, _ = should_skip("/repo/README.md", is_code_mode=True)
    assert skip is False


def test_expanded_secret_detection() -> None:
    """Verify detection of secret substrings, exact names, and sensitive directories."""
    assert is_secret("/home/user/.ssh/config") is True
    assert is_secret("/home/user/.gnupg/pubring.kbx") is True
    assert is_secret("/home/user/.aws/config") is True
    assert is_secret("/home/user/.kube/config") is True
    assert is_secret("/home/user/infra/terraform.tfstate") is True
    assert is_secret("/home/user/infra/terraform.tfstate.backup") is True
    assert is_secret("/home/user/k8s/prod_kubeconfig.yaml") is True
    assert is_secret("/home/user/ansible/hosts.yml") is True
    assert is_secret("/home/user/keys/deploy_ed25519") is True
    assert is_secret("/home/user/keys/backup_rsa.pub") is True
    assert is_secret("/home/user/config/app_secrets.yaml") is True
    assert is_secret("/home/user/config/db_credentials.ini") is True

    assert is_secret("/home/user/src/parser.py") is False
    assert is_secret("/home/user/docs/universal.md") is False
    assert is_secret("/home/user/ansible/ghosts.yml") is False


def test_minified_detection_and_size_limit_constant() -> None:
    """Verify minified bundle patterns and MAX_TEXT_FILE_BYTES constant."""
    assert MAX_TEXT_FILE_BYTES == 10 * 1024 * 1024
    for fname in ("app.min.js", "theme.min.css", "main.bundle.js", "vendor.chunk.js"):
        assert is_minified_file(f"/repo/dist/{fname}") is True
        skip, reason = should_skip(f"/repo/src/{fname}", is_code_mode=True)
        assert skip is True
        assert "minified" in reason
    assert is_minified_file("/repo/src/app.js") is False


def test_should_skip_exclusions_and_relative_path_boundary() -> None:
    """Verify should_skip honors excluded_folders, excluded_types, and Path.is_relative_to boundaries."""
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

    # Sibling prefix must NOT falsely match excluded folder
    assert is_path_excluded("/home/user/Archive2/keep.pdf", ["/home/user/Archive"]) is False

    # in_code_project suppresses non-screenshot files in general mode only
    skip_doc, _ = should_skip("/repo/README.md", is_code_mode=False, in_code_project=True)
    assert skip_doc is True
    skip_shot, _ = should_skip("/repo/Screenshot_1.png", is_code_mode=False, in_code_project=True)
    assert skip_shot is False
