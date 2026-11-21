# Multimodal Extractors & File Rules

SemSearch extracts text, raster and SVG images, PDFs, audio, video, and source code into standardized `ExtractedSegment` records (`semsearch.extractors`) while enforcing a **strict Zero-OCR invariant**: no OCR library or external OCR tool is ever invoked (nor does `SegmentKind.OCR` exist). Visual and acoustic segments are embedded directly into the shared multimodal vector space via Ollama's `/api/embed` API.

---

## 1. Document Extractor (`extractors/docs.py`)

Supports `.txt`, `.text`, `.md`, `.markdown`, `.rst`, `.rtf`, `.docx`, `.odt`, `.html`, and `.htm`.

!!! note "Legacy Binary `.doc` Removed"
    Legacy binary OLE2 `.doc` files are intentionally not supported and are rejected cleanly (`ValueError`) instead of producing corrupted text. Modern `.docx` and `.odt` documents are fully supported.

* **Format Parsing & Security Guards**:
  * **Zip-Bomb Protection (`MAX_ARCHIVE_UNCOMPRESSED_BYTES = 32 MiB`)**: Before extracting XML from `.docx` or `.odt` ZIP archives, SemSearch sums `info.file_size` across all archive entries and aborts if total uncompressed size exceeds **32 MiB**.
  * **Text File Size Limit (`MAX_TEXT_FILE_BYTES = 10 MiB`)**: Plain text, Markdown, HTML, and RTF files larger than **10 MiB** are skipped by the crawler and rejected by the extractor.
  * **Word (`.docx`)**: Extracts paragraphs and deduplicated table cells via `python-docx`.
  * **OpenDocument (`.odt`)**: Reads `content.xml` from the ZIP archive and extracts `<text:p>` and `<text:h>` elements.
  * **Rich Text (`.rtf`)**: Strips ignorable RTF groups (`{\*\...}`, `fonttbl`, `colortbl`, `stylesheet`, `info`, `pict`), decodes Unicode `\uN?` escapes (including signed UTF-16 values), decodes `\'xx` hex escapes using `cp1252`, and strips control words.
  * **HTML (`.html`, `.htm`)**: Parses DOM nodes via `_HTMLTextExtractor`, **discarding non-content subtrees inside `<script>`, `<style>`, `<noscript>`, `<svg>`, and `<head>`** while preserving block-level line breaks.
* **Word-Boundary Chunking (`chunk_document`)**:
  * Slices text into chunks of **1,800 characters** with a **200-character overlap**, snapping cleanly to whitespace boundaries.
  * Discards non-prose noise failing `has_words()` (< 20% alphabetic letters).
  * Emits `SegmentKind.CHUNK` (`Modality.TEXT`). Documents exceeding 40 chunks are batched in **40-chunk passes** (`info["last_chunk"]`, `FileState.PARTIAL` in `LONG_FILES`).

---

## 2. Two-Stage PDF Extractor (`extractors/pdfs.py`)

PDF extraction (`extract_pdf`) uses **PyMuPDF (`pymupdf`)** without OCR:

* **Encrypted PDF Handling**: Checks `doc.needs_pass` immediately and raises a controlled `ValueError("Encrypted PDF requires password")` so the file is recorded cleanly without hanging or crashing.
* **Per-Page Hybrid Extraction**:
  * **Digital Text Page (`SegmentKind.PAGE`, `Modality.TEXT`)**: If a page contains $\ge 200$ characters passing `has_words()`, its selectable text is indexed in FTS5 and embedded as text.
  * **Visual Page (`SegmentKind.PAGE_IMAGE`, `Modality.IMAGE`)**: Otherwise (scanned pages, charts, diagrams, slides), PyMuPDF renders the page at **150 DPI** capped at **1,400 px** maximum side, encodes it as JPEG base64, and embeds it visually with **Zero-OCR**.
* **Multi-Stage Batching (`max_pages = 30`)**: Indexes up to 30 pages per pass (`info["last_page"]`), scheduling remaining batches in `LONG_FILES` so short files stay responsive.

---

## 3. Image, SVG & Screenshot Extractor (`extractors/images.py`)

Supports `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tiff`, `.tif`, `.avif`, and `.svg` (`.heic` is excluded by default via `excluded_types = ["heic"]` unless removed in Settings).

* **Native `.svg` Vector Rasterization**: `.svg` files are rasterized natively via **PyMuPDF** (`fitz.open(path)` → `get_pixmap`), scaled to `1,024 px` (standard images) or `1,536 px` (screenshots), encoded as PNG base64, and embedded directly—requiring zero extra dependencies and zero OCR.
* **Decompression-Bomb Guard (`MAX_IMAGE_PIXELS = 100,000,000`)**: Enforces a 100-megapixel safety cap on Pillow (`Image.MAX_IMAGE_PIXELS`) and rejects oversized raster images before decoding full buffers.
* **Automatic Screenshot Classification**: Filenames starting with `screenshot`, `screen shot`, `cleanshot`, `scr-`, `captura de pantalla`, or `captura` classify as `FileKind.SCREENSHOT`; others classify as `FileKind.IMAGE`.
* **EXIF & Resolution Normalization (`extract_image`)**: Applies `ImageOps.exif_transpose()`, downscales with `LANCZOS` to **1,536 px** (screenshots) or **1,024 px** (images), and encodes screenshots/transparent assets as PNG and standard photos as JPEG (`quality=90`).

---

## 4. Audio & Video Extractor (`extractors/media.py`)

Requires `ffmpeg` (and `ffprobe`, bundled with FFmpeg) in your system `PATH`. Every `ffmpeg` invocation enforces **`-nostdin`**, a 1-hour stream cap (**`-t 3600`**), and a **`timeout=60`** subprocess limit.

* **Duration Probing (`probe_media_duration`)**: Calls `ffprobe` (`timeout=10`) during indexing and stores the real media length in `FileRecord.info["duration"]` for accurate UI timelines.
* **Audio Segmentation (`extract_audio`)**: Supports `.mp3`, `.m4a`, `.wav`, `.aac`, `.flac`, `.ogg`, `.wma`, and `.aiff`. Decodes to **16 kHz mono 16-bit PCM (`s16le`)**, slices into **30-second windows** with a **25-second hop**, drops silent windows (`RMS <= 0.003`), and emits base64 WAV `SegmentKind.AUDIO` (`Modality.AUDIO`) segments with `loc` / `loc_end`.
* **Video Keyframes & Soundtrack (`extract_video`)**: Supports `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`, `.m4v`, and `.wmv`. Samples 1 frame every **3.0 seconds** (capped at **1,024 px** width), deduplicates consecutive shots with 64-bit ($8 \times 8$) perceptual `average_hash` (dropping Hamming distance $\le 4$), emits `SegmentKind.FRAME` (`Modality.IMAGE`), and runs `extract_audio()` on the audio track.

---

## 5. Source Code & Notebook Extractor (`extractors/code.py`)

Supports **60+ programming languages** (`.py`, `.rs`, `.go`, `.ts`, `.tsx`, `.js`, `.jsx`, `.c`, `.cpp`, `.h`, `.java`, `.kt`, `.swift`, `.rb`, `.php`, `.cs`, `.sh`, `.sql`, `.vue`, `.svelte`, `.zig`, `.hs`, `.ex`, etc.), build manifests (`Makefile`, `Dockerfile`, `Containerfile`, `Justfile`, `Jenkinsfile`), and Jupyter Notebooks (`.ipynb`), bounded to **`MAX_CODE_FILE_BYTES = 10 MiB`**.

* **Whole-Line Stretch Chunking (`extract_code`)**: Accumulates lines up to **1,800 characters** (`SegmentKind.LINES`, `Modality.TEXT`), snaps backward up to 10 lines to natural boundaries (blank line or top-level non-indented statement, else 2-line overlap), and stores the 1-based line of the first top-level definition in `focus`.
* **Jupyter Notebooks (`extract_notebook`)**: Strips execution outputs and metadata, grouping non-empty `code` and `markdown` cells into 1-based `SegmentKind.CELLS` segments up to 1,800 characters.

---

## 6. Privacy, Crawler Rules & `.gitignore` (`core/rules.py`, `core/crawler.py`)

SemSearch enforces strict local filtering before any file is read or indexed:

* **Root-Scoped Top-Down Project Tracking**: `crawl_folder()` propagates `in_code_project` strictly **top-down beneath `root_path`** (activating only when a descendant directory `current_dir != root_path` contains a `PROJECT_MARKERS` file such as `.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.). It **never walks upward to `$HOME` or `/`**, preventing a stray `.git` or `pyproject.toml` in a parent folder from suppressing general document indexing.
* **`.gitignore` Awareness**: While descending inside code mode or any detected code project, `crawl_folder()` loads local `.gitignore` rules (supporting `#` comments, `!` negation, trailing `/` directory-only rules, anchored paths, and `**/` prefixes) and prunes ignored subtrees and files.
* **Strengthened Secret & Sensitive Folder Exclusion (`is_secret`)**:
  * **Sensitive Hidden Folders**: `.ssh`, `.gnupg`, `.aws`, `.kube`.
  * **Secret Extensions**: `.pem`, `.key`, `.p12`, `.pfx`, `.keystore`, `.jks`, `.ppk`, `.kdbx`, `.gpg`, `.asc`, `.crt`, `.cer`, `.der`, `.p8`, `.mobileprovision`, `.tfstate`.
  * **Secret Prefixes / Substrings / Exact Names**: `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `*_ed25519`, `*_rsa`, `.env*`, `*secret*`, `*credential*`, `*tfstate*`, `*kubeconfig*`, `.netrc`, `.npmrc`, `.pypirc`, `htpasswd`, `.htpasswd`, `hosts.yml`.
* **Minified Bundles & Build Trees**: Skips `.min.js`, `.min.css`, `.bundle.js`, `.chunk.js` (`is_minified_file`) and build/dependency trees (`node_modules`, `bower_components`, `venv`, `site-packages`, `__pycache__`, `DerivedData`, `Pods`, `Carthage`, `build`, `dist`, `target`, `vendor`, `.git`, `.noindex`).
* **Machine Dataset Protection (`is_dataset_folder`)**: Skips media files in subdirectories with $\ge 300$ media files of the same kind where $\ge 70\%$ have machine-generated names.
* **Orphan Pruning (`purge_outside_roots`)**: Removing a root folder in Settings atomically purges all previously indexed records outside the remaining active roots.
