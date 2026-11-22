"""Unit tests for file classification, secret detection, minified assets, and skip rules."""

from pathlib import Path

from semsearch.core.rules import (
    CODE_ALLOWED_DOT_DIRS,
    CODE_MANIFEST_FILENAMES,
    CODE_ONLY_SKIPPED_FOLDERS,
    DOC_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MAX_TEXT_FILE_BYTES,
    SKIPPED_FOLDER_NAMES,
    UNIVERSAL_SKIPPED_FOLDERS,
    classify_file,
    is_dataset_folder,
    is_inside_code_project,
    is_minified_file,
    is_path_excluded,
    is_secret,
    looks_machine_named,
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


def test_context_sensitive_skipped_folders_and_allowed_dot_dirs() -> None:
    """Verify CODE_ONLY_SKIPPED_FOLDERS apply only in code context while UNIVERSAL_SKIPPED_FOLDERS always apply."""
    assert SKIPPED_FOLDER_NAMES == UNIVERSAL_SKIPPED_FOLDERS | CODE_ONLY_SKIPPED_FOLDERS
    assert {".github", ".devcontainer", ".circleci"} <= CODE_ALLOWED_DOT_DIRS
    assert ".git" not in CODE_ALLOWED_DOT_DIRS
    assert ".ssh" not in CODE_ALLOWED_DOT_DIRS

    # General mode outside code projects does NOT skip vendor/target/build/dist
    assert should_skip("/home/user/Documents/vendor/invoice.pdf", is_code_mode=False)[0] is False
    assert should_skip("/home/user/Documents/Sales/target/q4.pdf", is_code_mode=False)[0] is False

    # Code mode and nested code projects DO skip CODE_ONLY_SKIPPED_FOLDERS
    skip_code, reason_code = should_skip("/home/user/Develop/app/target/main.rs", is_code_mode=True)
    assert skip_code is True
    assert "skipped folder target" in reason_code

    skip_proj, reason_proj = should_skip(
        "/home/user/Documents/app/vendor/Screenshot_1.png",
        is_code_mode=False,
        in_code_project=True,
    )
    assert skip_proj is True
    assert "skipped folder vendor" in reason_proj

    # Universal skipped folders always apply
    assert should_skip("/home/user/Documents/node_modules/manual.pdf", is_code_mode=False)[0] is True


def test_looks_machine_named_camera_timestamps_vs_datasets() -> None:
    """Verify phone/camera timestamp stems are not treated as machine-generated dataset files."""
    for camera_stem in (
        "20260815_143022",
        "20260815-143022",
        "20260815_143022_1",
        "20260815_143022-2",
        "2026-08-15_14-30-22",
        "2026-08-15_14-30-22_1",
        "2026-08-15 14.30.22",
    ):
        assert looks_machine_named(camera_stem) is False

    for dataset_stem in ("frame_0001", "0001", "a1b2c3d4e5f6", "sample_0042", "20269999_999999"):
        assert looks_machine_named(dataset_stem) is True

    phone_photos = [f"/photos/20260815_{10 + (i // 3600):02d}{(i // 60) % 60:02d}{i % 60:02d}.jpg" for i in range(350)]
    assert is_dataset_folder(phone_photos) is False


def test_is_secret_allows_user_media_for_generic_words_only() -> None:
    """Verify generic secret/credential words do not block user documents/media while blocking configs/keys."""
    for user_file in (
        "/home/user/Books/The Secret Garden.pdf",
        "/home/user/Docs/Secret_Recipe.docx",
        "/home/user/Docs/credentials_letter.odt",
        "/home/user/Docs/secret_memo.rtf",
        "/home/user/Photos/secret_santa.jpg",
        "/home/user/Music/secret_track.mp3",
        "/home/user/Videos/top_secret_movie.mp4",
    ):
        assert is_secret(user_file) is False

    for secret_file in (
        "/home/user/config/secrets.json",
        "/home/user/config/secret.txt",
        "/home/user/config/credentials.toml",
        "/home/user/config/secret.cfg",
        "/home/user/config/secret.conf",
        "/home/user/src/credentials.py",
        "/home/user/credentials",
        "/home/user/.ssh/secret.pdf",
        "/home/user/keys/backup_id_rsa.pdf",
        "/home/user/.env.pdf",
        "/home/user/certs/secret.pem",
    ):
        assert is_secret(secret_file) is True


def test_code_manifest_filenames_allowed_in_code_mode() -> None:
    """Verify project manifests are indexed in code mode while generic data/lock files remain skipped."""
    assert {"pyproject.toml", "cargo.toml", "package.json", "tsconfig.json", "pom.xml", "composer.json"} == (
        CODE_MANIFEST_FILENAMES
    )
    for manifest in ("pyproject.toml", "Cargo.toml", "package.json", "tsconfig.json", "pom.xml", "composer.json"):
        skip, _ = should_skip(f"/repo/{manifest}", is_code_mode=True)
        assert skip is False

    for data_file in ("data.json", "settings.toml", "config.xml", "package-lock.json", "poetry.lock"):
        skip, reason = should_skip(f"/repo/{data_file}", is_code_mode=True)
        assert skip is True
        assert "data format" in reason


def test_multilingual_and_linux_tool_screenshot_prefixes() -> None:
    """Verify expanded multilingual and Linux screenshot tool prefixes classify as FileKind.SCREENSHOT."""
    for filename in (
        "Capture d'écran 2026-08-15.png",
        "Capture d\u2019écran 2026-08-15.png",
        "Bildschirmfoto_20260815.png",
        "Schermata_20260815.png",
        "Zrzut ekranu 2026.png",
        "Schermafbeelding_2026.png",
        "Снимок экрана_2026.png",
        "Знімок екрана_2026.png",
        "Знімак экрана_2026.png",
        "Ekran görüntüsü_2026.png",
        "flameshot_20260815.png",
        "Spectacle_20260815.png",
        "grim-20260815.png",
    ):
        assert classify_file(f"/home/user/Pictures/{filename}") == FileKind.SCREENSHOT


def test_is_inside_code_project(tmp_path: Path) -> None:
    """Verify is_inside_code_project matches crawler top-down not-is-root project boundary semantics."""
    # Ancestor above root has .git (e.g. $HOME dotfiles)
    (tmp_path / ".git").mkdir()

    docs_root = tmp_path / "Documents"
    docs_root.mkdir()
    # Even if root itself has a marker, files directly under root are NOT treated as inside a nested project
    (docs_root / ".git").mkdir()
    top_note = docs_root / "notes.md"
    top_note.write_text("notes")

    nested_proj = docs_root / "my_app"
    nested_proj.mkdir()
    (nested_proj / "pyproject.toml").write_text("[project]\nname='app'\n")
    proj_readme = nested_proj / "README.md"
    proj_readme.write_text("readme")
    deep_doc = nested_proj / "docs" / "design.md"
    deep_doc.parent.mkdir()
    deep_doc.write_text("design")

    outside_file = tmp_path / "other.md"
    outside_file.write_text("outside")

    assert is_inside_code_project(str(top_note), [str(docs_root)]) is False
    assert is_inside_code_project(str(proj_readme), [str(docs_root)]) is True
    assert is_inside_code_project(str(deep_doc), [str(docs_root)]) is True
    assert is_inside_code_project(str(outside_file), [str(docs_root)]) is False
    # Deepest matching root stops the walk
    assert is_inside_code_project(str(proj_readme), [str(docs_root), str(nested_proj)]) is False
