# crawler-engine Specification

## Purpose
The crawler-engine capability handles safe filesystem scanning, top-down project and `.gitignore` tracking strictly scoped under indexed roots, rule-based file classification and secret/noise exclusion, atomic candidate reconciliation, and orphan record purging.

## Requirements

### Requirement: File Rules, Classification, and Supported Extensions
The system MUST classify files into domain kinds (`FileKind`), including English and Spanish screenshot filename prefixes (`screenshot`, `screen shot`, `cleanshot`, `scr-`, `captura de pantalla`, `captura`), `.svg` in `IMAGE_EXTENSIONS`, and repository documentation (`.md`, `.markdown`, `.rst`) in code mode. Legacy binary `.doc` MUST NOT be present in `DOC_EXTENSIONS`.

#### Scenario: Classifying SVG images and Spanish screenshots while rejecting legacy binary .doc
- **WHEN** `classify_file()` evaluates `diagram.svg`, `Captura de pantalla_20260927_044528.png`, and `legacy.doc`
- **THEN** `diagram.svg` maps to `FileKind.IMAGE`, the Spanish capture maps to `FileKind.SCREENSHOT`, and `legacy.doc` returns `None`

### Requirement: Secret, Sensitive Folder, Minified Asset, and Size Protection
The system MUST exclude sensitive hidden folders (`.ssh`, `.gnupg`, `.aws`, `.kube`), secret extensions (`.pem`, `.key`, `.p12`, `.pfx`, `.keystore`, `.jks`, `.ppk`, `.kdbx`, `.gpg`, `.asc`, `.crt`, `.cer`, `.der`, `.p8`, `.mobileprovision`, `.tfstate`), secret filename prefixes (`id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `.env`, `secret`, `credential`, `.netrc`, `.npmrc`, `.pypirc`, `htpasswd`, `.htpasswd`), secret substrings (`secret`, `credential`, `tfstate`, `kubeconfig`, `id_rsa`, `id_ed25519`, `_ed25519`, `_rsa`), exact sensitive names (`hosts.yml`), minified/bundled web assets (`*.min.js`, `*.min.css`, `*.bundle.js`, `*.chunk.js`), and text/code files exceeding `MAX_TEXT_FILE_BYTES` (`10 MiB`), alongside skipped dependency/build directories (`SKIPPED_FOLDER_NAMES`), user-configured `excluded_folders` and `excluded_types`, and machine-generated dataset folders (`>= 300` media files of the same kind with `>= 70%` machine-named stems).

#### Scenario: Blocking sensitive folders, secrets, minified assets, and oversized text files
- **WHEN** `should_skip()` or `crawl_folder()` encounters files inside `.ssh`/`.aws`/`.kube`/`.gnupg`, files matching `*secret*`, `*credential*`, `*.tfstate`, `kubeconfig`, `hosts.yml`, `*_ed25519`, `*_rsa`, `app.min.js`, `vendor.bundle.js`, or a `> 10 MiB` `.txt`/`.py` file
- **THEN** those files are skipped and excluded from discovered candidates

#### Scenario: Dataset folder detection
- **WHEN** a subdirectory contains 300 or more media files of the same kind where at least 70% have machine-generated stems (excluding standard camera prefixes)
- **THEN** `is_dataset_folder()` flags the folder and media files inside it are omitted in general mode

### Requirement: Root-Scoped Top-Down Project Propagation and `.gitignore` Filtering
`crawl_folder()` MUST propagate `in_code_project` strictly top-down beneath `root_path` (triggering only when a descendant directory `current_dir != root_path` contains a `PROJECT_MARKERS` entry or `.xcodeproj`/`.xcworkspace` bundle) and MUST NEVER walk upward toward `$HOME` or `/` to check parent markers. While descending inside code mode or any detected code project, `crawl_folder()` MUST load and evaluate local `.gitignore` rules (supporting comments, `!` negation, trailing `/` directory-only patterns, anchored paths, and `**/` prefixes) cascading down subdirectories.

#### Scenario: Parent marker above root_path does not poison general indexing
- **WHEN** a user's `$HOME` or an ancestor above `root_path` contains `.git` or `pyproject.toml`, and `crawl_folder(root_path, is_code=False)` scans a documents folder inside it
- **THEN** files directly under `root_path` are NOT treated as inside a code project and remain eligible for general indexing

#### Scenario: Respecting local `.gitignore` rules during descent
- **WHEN** `crawl_folder()` traverses a code repository or nested project directory containing a `.gitignore` file
- **THEN** ignored subdirectories are pruned from traversal and ignored files are excluded from candidates unless negated by a subsequent `!` rule

### Requirement: Transactional Candidate Sync and Orphan Root Purging
`sync_candidates(db, candidates, roots=None)` MUST reconcile additions, size/mtime/kind changes, inode-matched moves (preserving segments and vectors via `db.move_file()`), and deletions inside a single `with db.transaction():` block while preserving records under vanished roots. `purge_outside_roots(db, active_roots)` MUST atomically delete indexed file records falling outside all currently configured active root folders.

#### Scenario: Atomic crawl synchronization and inode move preservation
- **WHEN** `sync_candidates()` reconciles added, modified, moved, and deleted files
- **THEN** all database mutations execute inside one atomic transaction, unchanged inode moves call `move_file()` without re-embedding, and deleted or newly excluded files under mounted roots are removed

#### Scenario: Purging orphaned records after removing a folder in Settings
- **WHEN** a user removes a root directory from configuration and `purge_outside_roots(db, active_roots)` runs
- **THEN** all previously indexed file records outside the remaining active roots are deleted cleanly in a single transaction
