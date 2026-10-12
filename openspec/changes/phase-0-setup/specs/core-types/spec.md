# Spec Delta

## Purpose

The core-types capability defines domain entities, enums, records, and search hit structures mirroring DigUp to provide strong typing and clean separation between extraction, storage, and GUI representation.

## ADDED Requirements

### Requirement: Domain Enums and Data Records
The system MUST define domain enums (`FileKind`, `SegmentKind`, `Modality`, `FileState`, `EngineActivity`) and dataclasses (`Candidate`, `FileRecord`, `SegmentRecord`, `SearchHit`, `ResultRow`, `EngineStatus`).

#### Scenario: Instantiate records and models
- **WHEN** domain records such as `Candidate`, `FileRecord`, `SegmentRecord`, `SearchHit`, `ResultRow`, and `EngineStatus` are instantiated with valid attributes
- **THEN** their values are preserved, typed, and structured for downstream pipeline processing
