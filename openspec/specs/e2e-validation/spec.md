# e2e-validation Specification

## Purpose
Provides comprehensive end-to-end integration testing, synthetic realistic testbed validation across all modalities, search fidelity benchmarks, CLI activation, and system architectural invariant enforcement.

## Requirements

### Requirement: Multimodal Testbed Generation and Ingestion
The testbed SHALL generate valid synthetic sample files across all supported modalities without requiring external internet access. The testbed SHALL provide raster images and desktop screenshots, digital and scanned visual PDFs, formatted documents, audio recordings with speech and silence segments, video clips with distinct scenes, and source code files with symbols alongside Jupyter notebooks with cleared outputs.

#### Scenario: Generating synthetic testbed artifacts across all modalities
- **WHEN** the synthetic testbed generation procedure is invoked with a target directory
- **THEN** valid test files for images, PDFs, documents, audio, video, code, and notebooks are generated successfully

#### Scenario: Ingestion of generated testbed files into the indexing pipeline
- **WHEN** the indexing pipeline processes the generated testbed directory
- **THEN** all supported testbed files are extracted, vectorized, and cataloged in the search databases

### Requirement: End-to-End Pipeline Indexing and Storage
The system SHALL execute full end-to-end indexing on the testbed directory structure, discovering all eligible files, delegating to appropriate extractors, generating vector embeddings, and populating primary and code database schemas. The system SHALL support incremental reindexing upon file modification and deletion.

#### Scenario: Initial complete indexation of multimodal directory
- **WHEN** an initial full crawl and index operation is executed against a test directory
- **THEN** all files are processed into database records and searchable vectors with zero failed files

#### Scenario: Incremental change detection and database update
- **WHEN** an existing indexed file is modified or removed from the watched directory
- **THEN** the index records and vector embeddings are updated or removed to mirror the file system state

### Requirement: Cross-Modal and Keyword Search Fidelity
The search engine SHALL retrieve multimodal items matching user textual intent across modalities. Natural language visual descriptions SHALL retrieve matching image and video frame segments; exact serials and identifier tokens SHALL trigger lookup scoring with BM25 boost; and queries starting with the code prefix SHALL target the code database with definition line anchoring.

#### Scenario: Cross-modal visual retrieval from text description
- **WHEN** a user enters a descriptive textual query matching visual contents of an image or video segment
- **THEN** the target image or video frame segment is returned in the primary ranked results

#### Scenario: Serial and identifier lookup retrieval
- **WHEN** a user enters an identifier, hexadecimal string, or hyphenated serial code
- **THEN** lookup routing is applied and the exact matching item is boosted to the top rank

#### Scenario: Code search with definition line target
- **WHEN** a user issues a search query prefixed with code targeting a function or class symbol
- **THEN** the code database is searched and the matching file is returned with its definition line anchor

### Requirement: Quality Gating and Weaker Match Folding
The search engine SHALL enforce quality gating by discarding result candidates whose normalized Z-score falls below 2.0 and whose query coverage falls below 0.5. Search candidates scoring below 50% of the top candidate score SHALL be partitioned into a folded weaker matches collection.

#### Scenario: Filtering candidate segments failing quality threshold
- **WHEN** a search query produces candidate segments with Z-score below 2.0 and coverage below 0.5
- **THEN** those candidate segments are eliminated from both primary and weaker result sets

#### Scenario: Partitioning results below 50% top score into weaker matches
- **WHEN** search results contain qualifying hits scoring below half of the highest candidate score
- **THEN** those hits are separated into the weaker matches list and hidden until explicitly unfolded

### Requirement: CLI Activation and IPC Message Dispatch
The command-line interface SHALL support flags to trigger the quick search panel, settings dialog, and background daemon mode, while defaulting to background system tray daemon mode when invoked without flags. Invocations of the CLI while an instance is already running SHALL forward the requested action to the active instance via local inter-process communication and terminate the invoking process immediately.

#### Scenario: Dispatching quick search panel from command line
- **WHEN** the command-line interface is launched with the panel flag
- **THEN** the floating quick search panel is displayed and focused

#### Scenario: Dispatching full search window from command line
- **WHEN** the command-line interface is launched without flags or with the daemon flag
- **THEN** the application starts anchored in the system tray without opening a search window or panel

#### Scenario: Dispatching settings dialog from command line
- **WHEN** the command-line interface is launched with the settings flag
- **THEN** the preferences settings dialog is displayed and focused

#### Scenario: Forwarding CLI command to existing running instance
- **WHEN** a second CLI process is launched while an instance is already active
- **THEN** the command is forwarded over local socket communication to the active instance and the secondary process exits

### Requirement: Strict Zero OCR and Multimodal Model Invariants
The system SHALL strictly maintain the zero OCR architectural invariant across all image and PDF extractors without importing or executing any optical character recognition engines. The system configuration SHALL default to the verified multimodal model `embeddinggemma-2` and prevent selection of non-multimodal models.

#### Scenario: Enforcing zero OCR invariant across extractors
- **WHEN** image, screenshot, and PDF files are ingested and extracted
- **THEN** visual contents are processed purely via native renderers and vision embeddings without calling OCR tools

#### Scenario: Enforcing multimodal model default configuration
- **WHEN** application configuration is initialized or inspected
- **THEN** the default embedding model is set to `embeddinggemma-2` and model selection options are restricted to multimodal models

### Requirement: Desktop Deep Linking Action Dispatch
The system SHALL dispatch native desktop actions corresponding to result kinds and hit locations. Code results with target lines SHALL invoke the code editor at the specified line; PDF results with page targets SHALL launch document viewers at the specified page; and media results with timestamp offsets SHALL launch media players at the target timestamp.

#### Scenario: Activating code result at target line
- **WHEN** a code search hit with a valid line anchor is activated
- **THEN** the configured or auto-detected code editor is launched targeting the specific file and line

#### Scenario: Activating PDF result at target page
- **WHEN** a PDF search hit with a page number is activated
- **THEN** a compatible PDF viewer is launched with the page navigation parameter

#### Scenario: Activating media result at target timestamp
- **WHEN** an audio or video search hit with a timestamp offset is activated
- **THEN** a compatible media player is launched seeking to the target playback timestamp

#### Scenario: Revealing result item in file manager
- **WHEN** the reveal file manager action is triggered for any search hit
- **THEN** the system file manager is opened with the target file highlighted in its parent folder
