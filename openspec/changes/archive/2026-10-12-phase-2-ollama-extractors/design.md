# Design

## Context

Phase 1 established SQLite WAL storage, NumPy vector math, filtering rules, and crawler reconciliation. Phase 2 introduces the inference client connecting to local Ollama and the multimodal content extraction pipeline for desktop files.

A core tenet of SemSearch is radical simplicity (KISS), strictly top-level PEP 8 imports, zero defensive bloat, and zero optical character recognition (OCR). Instead of running brittle and compute-intensive OCR libraries on images or scanned documents, visual documents are encoded directly into image payloads and embedded natively using Ollama multimodal vision models (defaulting to `embeddinggemma-2`).

## Goals / Non-Goals

**Goals:**
- Provide `OllamaClient` in `src/semsearch/core/ollama.py` supporting `/api/tags`, `/api/show`, and `/api/embed`.
- Restrict model selection to verified multimodal models by inspecting vision/audio capabilities via `/api/show`.
- Support batch embedding of text strings, image objects `{"image": "<base64>"}`, and audio objects `{"audio": "<base64>"}` with automatic batch splitting and timeout controls.
- Implement dedicated file extractors returning structured `ExtractedSegment` objects for:
  - Images and screenshots (`images.py`): Pillow downscaling (1024px / 1536px), EXIF orientation, zero OCR.
  - PDFs (`pdfs.py`): PyMuPDF two-stage page reading (1–30 pass 1, remainder deferred); >= 200 chars emits text, < 200 chars emits 1400px rendered page image; strictly zero OCR.
  - Documents (`docs.py`): text extraction for txt, md, html (tag stripped), docx, odt, rtf; 1800 char chunk size, 200 char overlap snapped to word boundaries, `has_words` filter (>= 20% letters).
  - Media (`media.py`): audio (16 kHz mono WAV, 30s window / 25s hop, RMS silence filter > 0.003) and video (3s interval, 8x8 average perceptual hash deduplication dropping Hamming <= 4 bits, keyframes + soundtrack).
  - Source code and notebooks (`code.py`): whole-line chunking (~1800 chars), top-level definition detection (`focus` line), and Jupyter notebook output stripping.

**Non-Goals:**
- Calling external/cloud LLM APIs (Ollama local inference only).
- Incorporating OCR engines (Tesseract, EasyOCR, or PaddleOCR are strictly forbidden).
- Background worker threads or scheduler services (handled in Phase 3).
- UI widgets or preferences dialog implementation (handled in Phase 4).

## Decisions

### 1. Data Contracts and Segment Representation

All extractors produce a uniform dataclass `ExtractedSegment`:

```python
from dataclasses import dataclass
from semsearch.core.types import Modality, SegmentKind


@dataclass
class ExtractedSegment:
    kind: SegmentKind
    modality: Modality
    loc: float = 0.0
    loc_end: float = 0.0
    excerpt: str = ""
    payload: str | dict[str, str] = ""
    focus: float = 0.0
```

- When `modality == Modality.TEXT`, `payload` is `str`.
- When `modality == Modality.IMAGE`, `payload` is `{"image": "<base64_jpeg_or_png>"}`.
- When `modality == Modality.AUDIO`, `payload` is `{"audio": "<base64_wav>"}`.

### 2. Ollama Client Architecture (`src/semsearch/core/ollama.py`)

- **Class Structure**:
  ```python
  class OllamaClient:
      def __init__(
          self,
          base_url: str = "http://localhost:11434",
          timeout: float = 30.0,
          embed_timeout: float = 120.0,
          max_batch_size: int = 32,
      ) -> None: ...

      def get_tags(self) -> list[dict]: ...
      def show_model(self, model: str) -> dict: ...
      def is_multimodal(self, model_info: dict) -> bool: ...
      def list_multimodal_models(self) -> list[str]: ...
      def embed(
          self,
          model: str,
          inputs: list[str | dict[str, str]],
          truncate: bool = True,
      ) -> list[list[float]]: ...
  ```
- **Capability Filtering**: `is_multimodal` inspects `details.families`, `model_info.architecture`, and projector metadata for indicators of vision or audio support (e.g., `clip`, `mllama`, `gemma2`, `vision`). Models without multimodal projectors are filtered out.
- **Batching & Timeouts**: `embed()` partitions the `inputs` list into slices of `max_batch_size` (default 32) and issues `POST /api/embed` with an extended timeout (`embed_timeout = 120.0`). Metadata requests (`/api/tags`, `/api/show`) use `timeout = 30.0`.
- **Exceptions**: Custom domain exceptions `OllamaError`, `OllamaConnectionError`, `OllamaTimeoutError`, and `OllamaModelError` map underlying `httpx` exceptions.

### 3. Image Extractor (`src/semsearch/extractors/images.py`)

- **Signature**:
  ```python
  def extract_image(path: str, is_screenshot: bool = False) -> ExtractedSegment | None: ...
  ```
- **Downscaling**: Standard images are scaled to fit within a `1024x1024` bounding box; screenshots (`is_screenshot=True`) are scaled to fit within `1536x1536` to retain UI text sharpness.
- **EXIF Handling**: `PIL.ImageOps.exif_transpose` corrects camera orientation before downscaling.
- **Output**: RGB images are saved to an in-memory JPEG buffer (quality 85); images with alpha are saved as PNG. The resulting bytes are encoded to standard base64 strings. Strictly zero OCR is performed.

### 4. PDF Extractor (`src/semsearch/extractors/pdfs.py`)

- **Signature**:
  ```python
  def extract_pdf(
      path: str,
      max_pages: int = 30,
      start_page: int = 0,
  ) -> list[ExtractedSegment]: ...
  ```
- **Two-Stage Reading**: Pass 1 extracts pages `0` to `max_pages - 1` (default 30). Pages beyond 30 are deferred to background passes.
- **Text vs. Visual Threshold**:
  - `page.get_text("text").strip()` is inspected.
  - If `len(text) >= 200`: emits `SegmentKind.PAGE`, `Modality.TEXT`, with `loc = page_idx + 1`.
  - If `len(text) < 200`: the page is treated as a scanned document, slide, or diagram. It is rendered via PyMuPDF matrix scaling to a 1400px bounding dimension (`zoom = 1400.0 / max(page.rect.width, page.rect.height)`) and emitted as `SegmentKind.PAGE_IMAGE`, `Modality.IMAGE`, with `payload = {"image": "<base64>"}`. Strictly zero OCR.

### 5. Document Extractor (`src/semsearch/extractors/docs.py`)

- **Signatures**:
  ```python
  def has_words(text: str, min_letter_ratio: float = 0.20) -> bool: ...
  def strip_html(html_text: str) -> str: ...
  def read_doc_text(path: str) -> str: ...
  def chunk_text(
      text: str,
      chunk_size: int = 1800,
      overlap: int = 200,
  ) -> list[tuple[int, int, str]]: ...
  def extract_doc(path: str) -> list[ExtractedSegment]: ...
  ```
- **Chunking with Word Boundaries**: Slices text into spans of length `chunk_size` (1800). If the cut falls within a word, it walks backwards up to `overlap` (200) characters to snap to the nearest whitespace or newline.
- **Quality Filter**: `has_words` calculates `sum(1 for c in text if c.isalpha()) / max(len(text), 1)`. Chunks with a letter ratio below 0.20 are discarded.

### 6. Media Extractor (`src/semsearch/extractors/media.py`)

- **Signatures**:
  ```python
  def extract_audio(
      path: str,
      target_sr: int = 16000,
      window_sec: float = 30.0,
      hop_sec: float = 25.0,
      min_rms: float = 0.003,
  ) -> list[ExtractedSegment]: ...


  def average_hash(im: Any, hash_size: int = 8) -> int: ...
  def hamming_distance(h1: int, h2: int) -> int: ...
  def extract_video(
      path: str,
      sample_interval_sec: float = 3.0,
      hash_threshold: int = 4,
  ) -> list[ExtractedSegment]: ...
  ```
- **Audio Processing**: Decodes audio to 16 kHz mono 16-bit PCM WAV. Windows of 30s (`480,000` samples) advance with a 25s hop (`400,000` samples). Chunks with `RMS <= 0.003` are discarded as silent. Non-silent chunks are packaged as in-memory WAV base64 payloads with `SegmentKind.AUDIO`.
- **Video Processing**: Samples frames every 3.0 seconds. Computes an 8x8 average perceptual hash (aHash) for each frame. If `hamming_distance(current_hash, previous_hash) <= 4`, the frame is dropped as duplicate. Unique keyframes are downscaled to 1024px and emitted as `SegmentKind.FRAME`. Audio is simultaneously extracted and chunked.

### 7. Code Extractor (`src/semsearch/extractors/code.py`)

- **Signatures**:
  ```python
  def extract_code(
      path: str,
      chunk_chars: int = 1800,
      overlap_lines: int = 3,
  ) -> list[ExtractedSegment]: ...
  def extract_notebook(path: str) -> list[ExtractedSegment]: ...
  ```
- **Whole-Line Slicing**: Code files are read line by line. Lines are grouped until reaching ~1800 characters, preserving exact newline boundaries without splitting expressions. Overlap preserves 3 context lines.
- **Definition Detection**: Uses regex patterns for definitions (`def `, `class `, `fn `, `function `, `func `, `pub fn `, `struct `, `interface `). The first definition found in a chunk sets `focus = line_number` for direct jump-to-symbol navigation.
- **Jupyter Notebooks**: Parses JSON structure, removes all `outputs`, `execution_count`, and notebook-level metadata, concatenating code and markdown sources into whole-line chunks.

## Risks / Trade-offs

- **High memory from base64 visual payloads**: Base64 strings increase payload size by ~33%. → Images are downscaled (1024px/1400px/1536px) and compressed to JPEG with quality 85, keeping individual base64 strings under 300 KB.
- **Ollama timeout during batch inference**: Processing large batches of images can take seconds. → Requests are split into chunks of `max_batch_size = 32`, and the embedding timeout is configured to 120 seconds.
- **Absence of OCR on text-only models**: Without OCR, text inside images/scanned PDFs is only searchable via vision embeddings. → Multimodal models natively comprehend visual text in diagrams, slides, and scans without OCR pipeline brittle failures.
- **FFmpeg / system media codec availability**: Audio/video decoding requires media codecs. → Python standard library `wave` is used for uncompressed WAV; external decoders (e.g., PyMuPDF pixmaps or system ffmpeg) are invoked cleanly with fallback handling.
