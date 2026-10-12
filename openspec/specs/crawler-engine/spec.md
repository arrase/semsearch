# crawler-engine Specification

## Purpose
The crawler-engine capability handles filesystem scanning, rule-based file classification and exclusion, and reconciliation of discovered candidates against database records.

## Requirements

### Requirement: File Rules and Classification
The system MUST classify files into domain kinds and identify skipped folders, secret credentials, code project boundaries, and machine-generated dataset folders.

#### Scenario: Secret and rule evaluation
- **WHEN** files with credential patterns, secret extensions, or inside ignored directories are evaluated
- **THEN** `should_skip` indicates that the files must be skipped with appropriate reasons

#### Scenario: Dataset folder detection
- **WHEN** a folder contains 300 or more media files of the same kind where 70% or more have machine-generated names
- **THEN** `is_dataset_folder` identifies the folder as a dataset to be excluded

### Requirement: Directory Crawling and Candidate Synchronization
The system MUST crawl target folders using directory scanning, discover candidate files, and reconcile them against the database to detect additions, changes, moves, and deletions.

#### Scenario: Crawl and sync filesystem changes
- **WHEN** a directory with new files, modified files, moved files, and deleted files is crawled and synchronized
- **THEN** candidate additions are inserted as pending, modifications are requeued, inode-matched moves update paths without re-embedding, and vanished files are pruned
