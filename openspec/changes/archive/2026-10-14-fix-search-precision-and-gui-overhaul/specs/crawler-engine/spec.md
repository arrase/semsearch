# crawler-engine Specification Delta

## MODIFIED Requirements

### Requirement: File Rules and Classification
The system MUST classify files into domain kinds (including English and Spanish screenshot filename prefixes and repository documentation in code mode) and identify skipped folders, user-configured `excluded_folders` and `excluded_types`, secret credentials, code project boundaries, and machine-generated dataset folders.

#### Scenario: Secret and rule evaluation
- **WHEN** files with credential patterns, secret extensions, user-excluded extensions/folders, or inside ignored directories are evaluated
- **THEN** `should_skip` indicates that the files must be skipped with appropriate reasons

#### Scenario: Dataset folder detection
- **WHEN** a folder contains 300 or more media files of the same kind where 70% or more have machine-generated names
- **THEN** `is_dataset_folder` identifies the folder as a dataset to be excluded

#### Scenario: Spanish and Linux screenshot classification
- **WHEN** an image file is named with a Spanish screenshot prefix such as `Captura de pantalla_20260927_044528.png`
- **THEN** `classify_file` classifies the file as `FileKind.SCREENSHOT`

### Requirement: Directory Crawling and Candidate Synchronization
The system MUST crawl target folders using directory scanning while respecting `excluded_folders` and `excluded_types`, discover candidate files, and reconcile them against the database to detect additions, changes, moves, and deletions (including pruning previously indexed files that match current exclusion rules).

#### Scenario: Crawl and sync filesystem changes
- **WHEN** a directory with new files, modified files, moved files, and deleted or newly excluded files is crawled and synchronized
- **THEN** candidate additions are inserted as pending, modifications are requeued, inode-matched moves update paths without re-embedding, and vanished or excluded files are pruned
