# Spec Delta

## Purpose

Defines extraction contracts for transforming desktop files into standardized multimodal segment inputs across images, PDFs, office documents, audio, video, and code, strictly prohibiting OCR.

## ADDED Requirements

### Requirement: Zero OCR Invariant Across All Extractors
Extractors MUST NOT perform optical character recognition (OCR) on any images, rendered PDF pages, or video frames, relying exclusively on multimodal visual embeddings to capture visual and textual information.

#### Scenario: Visual document processing without OCR
- **WHEN** an image, scanned document, or PDF page is processed
- **THEN** no OCR library or process is invoked, and the raw visual content is converted directly to an image payload

### Requirement: Image and Screenshot Extraction
The image extractor MUST load images, normalize EXIF orientation, downscale to a maximum bounding dimension of 1024px for standard images and 1536px for screenshots, and return base64-encoded image payloads.

#### Scenario: Standard image extraction and downscaling
- **WHEN** an image exceeding 1024px in width or height is extracted
- **THEN** it is downscaled preserving aspect ratio to fit within 1024px, EXIF orientation is corrected, and a base64 image segment is returned

#### Scenario: Screenshot extraction with enhanced resolution
- **WHEN** a screenshot candidate file is extracted
- **THEN** it is downscaled to fit within 1536px bounding dimension preserving sharp text and UI elements, returned as a base64 image segment

### Requirement: PDF Two-Stage and Hybrid Extraction
The PDF extractor MUST read documents in two stages, extracting pages 1 to 30 in the initial pass and deferring subsequent pages. For each page, digital text MUST be extracted if the character count is at least 200; otherwise, the page MUST be rendered to a 1400px image segment without OCR.

#### Scenario: Digital text page extraction
- **WHEN** a PDF page contains 200 or more characters of selectable text
- **THEN** the text content is extracted as a text segment with its page number location

#### Scenario: Scanned or visual slide page rendering
- **WHEN** a PDF page contains fewer than 200 characters of selectable text
- **THEN** the entire page is rendered to an image scaled to 1400px dimension and emitted as an image segment with zero OCR

#### Scenario: Multi-stage pagination limit
- **WHEN** a PDF contains more than 30 pages
- **THEN** the first pass extracts up to page 30 and marks the remaining pages for deferred background processing

### Requirement: Document Text Extraction and Boundary-Snapped Chunking
The document extractor MUST parse plain text, Markdown, HTML (with tags stripped), DOCX, ODT, and RTF into chunks of at most 1800 characters with 200-character overlap snapped to word boundaries, discarding chunks with under 20% alphabetic letters.

#### Scenario: Markup stripping and word-boundary chunking
- **WHEN** a document containing markup or formatting is processed
- **THEN** plain text is extracted, stripped of markup tags, and segmented into chunks of up to 1800 characters with 200-character overlap snapped to whitespace

#### Scenario: Low-quality non-text filtering
- **WHEN** an extracted chunk contains fewer than 20% alphabetic characters
- **THEN** the chunk is discarded by the `has_words` filter

### Requirement: Audio and Video Media Extraction
The media extractor MUST convert audio to 16 kHz mono WAV, segmenting into 30-second windows with 25-second hops and filtering silence (RMS <= 0.003). For video, it MUST sample frames every 3 seconds, deduplicate frames using 8x8 average perceptual hashing (dropping Hamming distance <= 4), and extract the audio track.

#### Scenario: Audio windowing and silence filtration
- **WHEN** an audio file is extracted
- **THEN** 16 kHz mono 30-second chunks are generated at 25-second hop intervals, and chunks with RMS amplitude 0.003 or lower are discarded

#### Scenario: Video keyframe sampling and perceptual deduplication
- **WHEN** a video file is extracted
- **THEN** frames are captured at 3-second intervals, sequential frames with 8x8 perceptual hash Hamming distance <= 4 are discarded as duplicate shots, and remaining frames are emitted as image segments alongside audio soundtrack chunks

### Requirement: Code and Notebook Extraction with Definition Detection
The code extractor MUST slice source code into whole-line chunks of approximately 1800 characters, detect top-level function and class definition lines, and strip execution outputs and metadata from Jupyter notebooks.

#### Scenario: Whole-line code chunking and definition indexing
- **WHEN** source code files are parsed
- **THEN** chunks are split cleanly on line boundaries without splitting statements, and definition lines are identified and recorded in segment metadata

#### Scenario: Jupyter notebook output stripping
- **WHEN** a `.ipynb` notebook is extracted
- **THEN** all cell outputs, execution counts, and notebook metadata are stripped, extracting only clean code and markdown source cells
