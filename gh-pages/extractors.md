# Multimodal Extractors & File Rules

SemSearch extracts text, images, audio, video, and source code into unified `ExtractedSegment` records (`semsearch.extractors`) without relying on slow external OCR engines. Instead, visual and acoustic segments are embedded directly into the shared multimodal vector space via Ollama's `/api/embed` API.

---

## 1. Document Extractor (`extractors/docs.py`)

Supports `.txt`, `.text`, `.md`, `.markdown`, `.rst`, `.rtf`, `.docx`, `.doc`, `.odt`, `.html`, and `.htm`.

* **Format Parsing**:
  * **Word (`.docx`)**: Extracts paragraphs and deduplicated table cells via `python-docx`.
  * **OpenDocument (`.odt`)**: Reads `content.xml` from the ZIP archive and extracts `<text:p>` and `<text:h>` elements.
  * **Rich Text (`.rtf`)**: Strips font tables, color tables, stylesheets, hex escapes, and RTF control words.
  * **HTML (`.html`, `.htm`)**: Parses DOM nodes via `HTMLParser`, preserving block-level line breaks.
* **Word-Boundary Chunking (`chunk_document`)**:
  * Slices text into chunks of **1,800 characters** with a **200-character overlap**.
  * Snaps chunk boundaries cleanly to whitespace so words are never split mid-token.
  * Filters out chunks that lack alphabetic/numeric words (`has_words()`).
  * Produces `SegmentKind.CHUNK` segments with `Modality.TEXT`.

---

## 2. Two-Stage PDF Extractor (`extractors/pdfs.py`)

PDF extraction (`extract_pdf`) uses **PyMuPDF (`fitz`)** to handle both digital-text PDFs and scanned/slide-deck PDFs seamlessly:

* **Per-Page Evaluation**:
  * **Digital Text Page (`SegmentKind.PAGE`, `Modality.TEXT`)**: If a page contains $\ge 200$ characters of extractable text with valid words, its text is indexed directly in FTS5 and embedded as text.
  * **Visual Page (`SegmentKind.PAGE_IMAGE`, `Modality.IMAGE`)**: If a page has fewer than 200 characters (e.g., scanned pages, diagrams, charts, or presentation slides), PyMuPDF renders the page at **150 DPI** (capped at a maximum side of **1,400 px**), encodes it as JPEG base64, and embeds it visually via Ollama.
* **Multi-Stage Batching for Large PDFs**:
  * To prevent huge PDF books from blocking the indexing queue, `extract_pdf()` processes **30 pages per pass** (`max_pages = 30`).
  * After the first 30 pages are indexed, the file is moved to the `LONG_FILES` tier (`detail = "pages_done:30"`) so newly created files and shorter documents are indexed first before the remaining page batches complete.

---

## 3. Image & Screenshot Extractor (`extractors/images.py`)

Supports `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tiff`, `.tif`, `.avif`, and `.svg` (`.heic` is excluded by default unless enabled in settings).

* **Automatic Screenshot Classification**: Files whose names start with `screenshot`, `screen shot`, `cleanshot`, `scr-`, `captura de pantalla`, or `captura` are automatically classified as `FileKind.SCREENSHOT`; all other images are classified as `FileKind.IMAGE`.
* **EXIF & Resolution Normalization (`extract_image`)**:
  * Applies `ImageOps.exif_transpose()` so rotated phone photos are oriented upright before embedding.
  * Resizes images using `LANCZOS` resampling to a maximum dimension of **1,536 px for screenshots** (preserving crisp UI text rendered inside the screenshot) and **1,024 px for standard photos**.
  * Encodes screenshots and transparent images as **PNG** and standard photos as **JPEG (`quality=90`)** in memory, producing a `SegmentKind.IMAGE` (`Modality.IMAGE`) payload.

---

## 4. Audio & Video Extractor (`extractors/media.py`)

Requires `ffmpeg` in your system `PATH`.

### Audio Segmentation (`extract_audio`)
Supports `.mp3`, `.m4a`, `.wav`, `.aac`, `.flac`, `.ogg`, `.wma`, and `.aiff`.

1. Decodes the audio stream via `ffmpeg` to **16 kHz mono 16-bit signed PCM (`s16le`)** directly over `stdout` pipe.
2. Slices the waveform into **30-second windows** with a **25-second hop** (5-second overlap).
3. Computes the root-mean-square (RMS) energy of each window and discards silent windows (`rms <= 0.003`).
4. Encodes active windows as in-memory WAV base64 payloads (`SegmentKind.AUDIO`, `Modality.AUDIO`) annotated with start and end timestamps (`loc`, `loc_end`).

### Video Keyframe & Soundtrack Segmentation (`extract_video`)
Supports `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`, `.m4v`, and `.wmv`.

1. **Frame Sampling**: Samples 1 frame every **3.0 seconds** via `ffmpeg`, scaled to a maximum width of **1,024 px**.
2. **Perceptual Hash Deduplication (`average_hash`)**: Computes a 64-bit ($8 \times 8$) perceptual average hash (aHash) for each sampled frame. Consecutive frames with a Hamming distance $\le 4$ bits are skipped as static/duplicate shots.
3. **Multimodal Coverage**: Each unique visual scene is emitted as `SegmentKind.FRAME` (`Modality.IMAGE`, `loc = timestamp`), and `extract_audio()` is simultaneously run on the video's audio track so you can find videos either by what is shown on screen or by what is spoken.

---

## 5. Source Code & Notebook Extractor (`extractors/code.py`)

Supports **60+ programming languages** (`.py`, `.rs`, `.go`, `.ts`, `.tsx`, `.js`, `.jsx`, `.c`, `.cpp`, `.h`, `.java`, `.kt`, `.swift`, `.rb`, `.php`, `.cs`, `.sh`, `.sql`, `.vue`, `.svelte`, `.zig`, `.hs`, `.ex`, etc.), build files (`Makefile`, `Dockerfile`, `Containerfile`, `Justfile`, `Jenkinsfile`), and Jupyter Notebooks (`.ipynb`).

* **Whole-Line Stretch Chunking (`extract_code`)**:
  * Accumulates lines up to **1,800 characters** per segment (`SegmentKind.LINES`, `Modality.TEXT`).
  * Searches backward up to 10 lines for a **natural boundary**—either a blank line or a top-level non-indented line (excluding closing delimiters `})`). If no natural boundary exists, it splits cleanly and carries over a **2-line overlap**.
  * Detects top-level symbol definitions (`def`, `class`, `fn`, `function`, `func`, `pub fn`, `struct`, `interface`, `type`, `trait`, `enum`) via regex and stores the exact 1-based definition line in `focus`.
* **Jupyter Notebooks (`extract_notebook`)**:
  * Parses `.ipynb` JSON, extracts `code` and `markdown` cells while stripping heavy execution outputs and base64 plots, and groups cells into 1-based `SegmentKind.CELLS` segments up to 1,800 characters.

---

## 6. Privacy, Security & Noise Exclusion Rules (`core/rules.py`)

SemSearch enforces strict local filtering rules before any file is read or indexed:

* **Secret & Credential Protection (`is_secret`)**: Automatically skips files with sensitive extensions (`.pem`, `.key`, `.p12`, `.pfx`, `.keystore`, `.jks`, `.ppk`, `.kdbx`, `.gpg`, `.asc`, `.crt`, `.cer`, `.der`, `.p8`, `.mobileprovision`) or sensitive prefixes (`id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `.env`, `secret`, `credential`, `.netrc`, `.npmrc`, `.pypirc`, `htpasswd`, `.htpasswd`).
* **Dependency & Build Artifact Skipping (`SKIPPED_FOLDER_NAMES`)**: Skips hidden directories (`.git`, etc.) and build/vendor trees (`node_modules`, `bower_components`, `venv`, `site-packages`, `__pycache__`, `DerivedData`, `Pods`, `Carthage`, `build`, `dist`, `target`, `vendor`, `.noindex`).
* **Machine Dataset Protection (`is_dataset_folder`)**: Automatically detects folders containing $\ge 300$ media files of the same kind where $\ge 70\%$ have machine-generated hashes/numbers as filenames, preventing ML training datasets from polluting personal search results.
* **Code vs. General Isolation**: In general folder mode, source code files and non-screenshot assets inside recognized code repositories (directories containing `.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.) are automatically skipped so repositories only index when explicitly added to **Code Root Directories**.
