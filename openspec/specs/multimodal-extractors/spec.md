# multimodal-extractors Specification

## Purpose
Defines safe, bounded extraction contracts for transforming desktop files into standardized multimodal segment inputs across raster and SVG images, PDFs, office and markup documents, audio, video, and source code/notebooks, strictly enforcing the Zero-OCR invariant.

## Requirements

### Requirement: Strict Zero-OCR Invariant Across All Extractors
Extractors MUST NOT import or invoke any optical character recognition (OCR) library or process on images, SVGs, rendered PDF pages, or video frames. Visual content MUST be represented exclusively as multimodal image payloads (`SegmentKind.IMAGE`, `SegmentKind.PAGE_IMAGE`, `SegmentKind.FRAME`).

#### Scenario: Visual document processing without OCR
- **WHEN** an image, `.svg`, screenshot, scanned PDF page, or video frame is extracted
- **THEN** no OCR tool or library is invoked and the rasterized/normalized visual content is emitted directly as a base64 image segment

### Requirement: Image, SVG, and Screenshot Extraction with Decompression-Bomb Guard
`extract_image()` MUST enforce `MAX_IMAGE_PIXELS = 100_000_000` on Pillow and reject raster images exceeding that pixel budget. For `.svg` files, it MUST rasterize the first page natively via PyMuPDF (`fitz.open(path)`) bounded to `1024px` (images) or `1536px` (screenshots) and return a PNG base64 `SegmentKind.IMAGE` payload. For raster formats, it MUST apply `ImageOps.exif_transpose()`, downscale preserving aspect ratio via `LANCZOS` (`1024px` standard, `1536px` screenshot), and encode screenshots/transparent images as PNG and standard photos as JPEG (`quality=90`).

#### Scenario: SVG rasterization via PyMuPDF without external tools
- **WHEN** an `.svg` vector file is passed to `extract_image()`
- **THEN** PyMuPDF rasterizes the vector artwork scaled within `1024px` (or `1536px` for screenshots) and emits a PNG base64 `SegmentKind.IMAGE` segment without OCR

#### Scenario: Blocking oversized raster decompression bombs
- **WHEN** an image whose `width * height` exceeds `100_000_000` pixels is passed to `extract_image()`
- **THEN** extraction raises `ValueError` instead of allocating unbounded memory

### Requirement: PDF Two-Stage Hybrid Extraction and Encrypted PDF Handling
`extract_pdf()` MUST check `doc.needs_pass` and raise `ValueError("Encrypted PDF requires password")` cleanly for encrypted PDFs. It MUST process documents in batches of up to 30 pages (`max_pages = 30`). Pages with at least 200 characters passing `has_words()` MUST emit `SegmentKind.PAGE` (`Modality.TEXT`); all other pages MUST render at 150 DPI capped at 1400px maximum side as JPEG base64 `SegmentKind.PAGE_IMAGE` (`Modality.IMAGE`) without OCR.

#### Scenario: Controlled rejection of encrypted PDF
- **WHEN** `extract_pdf()` opens a password-protected PDF (`doc.needs_pass == True`)
- **THEN** it raises a controlled `ValueError` so the indexer records a clear failure detail without crashing

#### Scenario: Hybrid digital text and visual slide extraction with 30-page batching
- **WHEN** a PDF with more than 30 pages containing both dense text pages and image-only slides is extracted
- **THEN** the initial pass processes pages 1–30, emitting `PAGE` segments for `>= 200`-char text pages and `1400px`-capped `PAGE_IMAGE` segments for visual pages, and returns `is_complete = False`

### Requirement: Safe Document Extraction, Zip-Bomb Guard, HTML/RTF Cleaning, and Word-Boundary Chunking
`read_doc_text()` MUST support `.txt`, `.text`, `.md`, `.markdown`, `.rst`, `.rtf`, `.docx`, `.odt`, `.html`, and `.htm`, and MUST explicitly reject legacy binary `.doc` files (`ValueError`). Before reading `.odt` or `.docx` archives, it MUST verify total uncompressed archive size `<= MAX_ARCHIVE_UNCOMPRESSED_BYTES` (`32 MiB`) to prevent zip bombs. Plain text, HTML, and RTF reads MUST enforce `<= MAX_TEXT_FILE_BYTES` (`10 MiB`). `_HTMLTextExtractor` MUST discard content inside `<script>`, `<style>`, `<noscript>`, `<svg>`, and `<head>` tags. `_strip_rtf()` MUST discard ignorable RTF groups (`{\*\...}`, `fonttbl`, `colortbl`, `stylesheet`, `info`, `pict`), decode Unicode `\uN?` escapes, and decode `\'xx` hex escapes as `cp1252`. `chunk_document()` MUST produce `SegmentKind.CHUNK` segments up to 1800 characters with 200-character overlap snapped to whitespace, filtering chunks failing `has_words()`.

#### Scenario: Blocking zip bombs in ODT and DOCX archives
- **WHEN** an `.odt` or `.docx` archive reports total uncompressed entry sizes exceeding `32 MiB`
- **THEN** `read_doc_text()` raises `ValueError` before decompressing XML entries into memory

#### Scenario: Rejecting legacy binary .doc format
- **WHEN** a `.doc` file path is passed to `read_doc_text()`
- **THEN** it raises `ValueError` indicating legacy binary `.doc` is unsupported

#### Scenario: Cleaning HTML scripts/styles and decoding RTF cp1252 and Unicode escapes
- **WHEN** HTML containing `<head>`, `<script>`, `<style>`, `<noscript>`, or `<svg>` blocks or RTF containing `{\*\...}`, `\'e9`, and `\u241?` is parsed
- **THEN** non-content HTML tags and ignorable RTF groups are stripped while accented `cp1252` and Unicode characters are decoded accurately

### Requirement: Bounded Audio/Video Extraction and Duration Probing
`extract_audio()` and `extract_video()` MUST invoke `ffmpeg` with `-nostdin`, duration cap `-t 3600`, and subprocess `timeout=60`. Audio extraction MUST decode 16 kHz mono PCM, slice 30-second windows with 25-second hops, and discard silent windows (`RMS <= 0.003`). Video extraction MUST sample 1 frame every 3 seconds scaled to max width 1024px, deduplicate consecutive shots via 8x8 `average_hash` (dropping Hamming distance `<= 4`), and also extract the audio track. `probe_media_duration(path)` MUST query `ffprobe` (`timeout=10`) returning positive finite duration in seconds or `0.0` on failure.

#### Scenario: Enforcing ffmpeg subprocess bounds and probing duration
- **WHEN** `extract_audio()`, `extract_video()`, or `probe_media_duration()` runs on a media file
- **THEN** `ffmpeg` executes non-interactively (`-nostdin`) bounded to `-t 3600` and `timeout=60`, while `probe_media_duration()` returns the real duration in seconds via `ffprobe` (or `0.0` safely on failure)

### Requirement: Bounded Source Code and Notebook Extraction
`extract_code()` and `extract_notebook()` MUST enforce `MAX_CODE_FILE_BYTES = 10 MiB`. `extract_code()` MUST slice whole lines into ~1800-character `SegmentKind.LINES` segments snapping to natural boundaries (10 lines back, else 2-line overlap) and record top-level symbol definition line numbers in `focus`. `extract_notebook()` MUST extract 1-based `SegmentKind.CELLS` segments from `.ipynb` code and markdown cells while stripping execution outputs and metadata.

#### Scenario: Whole-line code chunking and Jupyter output stripping
- **WHEN** a source code file or `.ipynb` notebook within `10 MiB` is extracted
- **THEN** code files emit whole-line `LINES` segments with 1-based `focus` definition lines, and notebooks emit clean `CELLS` segments stripped of cell outputs
