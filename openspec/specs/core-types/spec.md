# core-types Specification

## Purpose
The core-types capability defines pure domain entities, enums, constants, and search hit structures with zero dependency on presentation or localization (`i18n`) layers, providing strong typing and clean separation between extraction, storage, search, and UI representation.

## Requirements

### Requirement: Pure Domain Enums and Data Records
The system MUST define domain enums (`FileKind`, `SegmentKind`, `Modality`, `FileState`, `EngineActivity`, `MatchReason`) and dataclasses (`ParsedQuery`, `Candidate`, `FileRecord`, `ExtractedSegment`, `SearchHit`, `EngineStatus`) inside `semsearch.core.types` without importing `semsearch.i18n` or any UI framework.

#### Scenario: Instantiate records and models
- **WHEN** domain records such as `ParsedQuery`, `Candidate`, `FileRecord`, `ExtractedSegment`, `SearchHit`, and `EngineStatus` are instantiated with valid attributes
- **THEN** their values are preserved, strongly typed, and structured for downstream pipeline processing without triggering localization or GUI side effects

### Requirement: Strict Zero-OCR Segment Enumeration
`SegmentKind` MUST enumerate only `NAME`, `IMAGE`, `PAGE`, `PAGE_IMAGE`, `CHUNK`, `AUDIO`, `FRAME`, `LINES`, and `CELLS`, and MUST NOT define an `OCR` member, enforcing the strict Zero-OCR architectural invariant at the type level.

#### Scenario: Inspecting supported segment kinds
- **WHEN** `SegmentKind` members are enumerated or validated
- **THEN** no `OCR` segment kind exists and visual content is represented exclusively via `IMAGE`, `PAGE_IMAGE`, or `FRAME`

### Requirement: Typed Match Reasons and Semantic Gate Constant
The system MUST define `SEMANTIC_Z_GATE = 2.2` and `MatchReason(StrEnum)` (`MEANING = "Matches meaning"`, `WORDS = "Matches words"`, `NAME = "Matches name"`). `SearchHit.reason` MUST return a `MatchReason` enum value (`MEANING` when `z >= SEMANTIC_Z_GATE`, `WORDS` when `keyword_rank > 0`, and `NAME` otherwise) so GUI and CLI presentation layers perform translation via `tr()` at the boundary.

#### Scenario: Deriving SearchHit match reason without i18n coupling
- **WHEN** `SearchHit.reason` is evaluated on hits with `z >= 2.2`, `keyword_rank > 0`, or filename-only matches
- **THEN** it returns `MatchReason.MEANING`, `MatchReason.WORDS`, or `MatchReason.NAME` respectively as pure domain enum values

### Requirement: Frozen Inline Query Filter Model
The system MUST define a frozen `ParsedQuery` dataclass capturing `clean_query: str`, `is_code: bool`, `kinds: frozenset[FileKind]`, `extensions: frozenset[str]`, `path_fragments: tuple[str, ...]`, and `after_mtime: float` for passing structured inline filter state across search components.

#### Scenario: Constructing immutable parsed queries
- **WHEN** `parse_query_filters()` produces a `ParsedQuery`
- **THEN** the returned object is immutable (`frozen=True`) and exposes normalized filter collections ready for database and search evaluation
