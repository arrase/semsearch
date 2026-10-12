# Design

## Context

Phase 1 established SQLite WAL persistence, schema v5, NumPy vector operations, and filesystem crawling. Phase 2 implemented the `OllamaClient` and multimodal extractors for images, PDFs, text/office documents, audio/video, and code files with zero OCR.

Phase 3 implements the services layer:
1. `SearchService` (`searcher.py`): executes hybrid search combining FTS5 BM25 keyword matching and cosine vector similarity fused via Z-scores using the DigUp ranking formula, with isolated `code:` routing, quality gating, and weaker matches folding.
2. `IndexService` (`indexer.py`): orchestrates background indexing via a two-stage pipelined worker (background extract/embed + main commit), multi-tier priority queues, dynamic cost tracking, time/disk estimation, and partial file continuation.
3. `FileSystemWatcher` (`watcher.py`): monitors filesystem changes using Linux inotify (`watchdog`) with differential debouncing for general vs code folders.
4. `PowerScheduler` (`scheduler.py`): enforces Linux power supply policies (`/sys/class/power_supply/`) and operating hours schedules.
5. Desktop actions (`actions.py`): deep links for media timestamps, PDF pages, code editor lines, and file manager reveals.

## Goals / Non-Goals

**Goals:**
- Provide `SearchService` supporting dual query mode (`code:` vs general), FTS5 BM25 keyword matching, vector similarity, Z-score fusion, DigUp ranking, gating, and weaker matches folding.
- Provide `IndexService` with two-stage pipelining (extract/embed decoupled from SQLite transaction commit), multi-tier priority scheduling (immediate <= 50 new files, full backfill, code index, long file continuation), and `EngineStatus` reporting.
- Provide `FileSystemWatcher` using Linux inotify with 0.75s debounce for general folders and 60s debounce (10m max ceiling) for code folders.
- Provide `PowerScheduler` checking `/sys/class/power_supply/` and time schedules (supporting overnight ranges) to pause/resume indexing.
- Provide Linux desktop deep links for media playback timestamps (`mpv`/`vlc`), PDF pages (`okular`/`evince`), code editor lines (VS Code, Zed, Cursor, Sublime, JetBrains), and file manager item reveals (DBus / CLI).

**Non-Goals:**
- Graphical user interface widgets (Phase 4).
- External or cloud-based embedding APIs (local Ollama only).
- Optical character recognition (zero OCR policy strictly maintained).
- Non-Linux OS platform adaptations (native Linux APIs used directly).

## Decisions

### 1. Search Service Architecture (`src/semsearch/services/searcher.py`)

- **Class Contract**:
  ```python
  class SearchService:
      def __init__(
          self,
          db: Database,
          code_db: Database | None = None,
          ollama: OllamaClient | None = None,
          model: str = "embeddinggemma-2",
      ) -> None: ...

      def search(self, query: str, limit: int = 50) -> list[SearchHit]: ...
      def format_results(
          self, hits: list[SearchHit], query: str
      ) -> tuple[list[ResultRow], list[ResultRow]]: ...
      def is_lookup_query(self, query: str) -> bool: ...
      def compute_coverage(self, terms: list[str], text: str) -> float: ...
  ```

- **Query Routing**:
  - If `query.strip().startswith("code:")`: route exclusively to `code_db` (using stripped query `query.strip()[5:].strip()`). If `code_db` is `None`, return empty results.
  - Otherwise: route exclusively to main `db`.

- **Lookup Query Detection**:
  - A query is detected as a lookup query if it contains programming syntax, camelCase, snake_case, dots, path slashes, serial tokens (e.g. `ABC-123`, `0x7fa`), or is a single token with digits.
  - Standard weights: $W_{\text{meaning}} = 1.0$, $W_{\text{words}} = 1.0$.
  - Lookup weights: $W_{\text{meaning}} = 0.5$, $W_{\text{words}} = 3.0$ (boosting exact lexical matches).

- **Query Embedding & Vector Similarity**:
  - Prompt prefixes:
    - General queries: `"task: search document | query: " + clean_query`
    - Code queries: `"task: search code | query: " + clean_query`
  - Generate query embedding vector using `ollama.embed(model, [prompted_query])`.
  - Calculate cosine similarity across stored vectors via `cosine_similarity(query_vec, doc_matrix)`.
  - Normalize similarities across modalities via `z_score_normalize(cosines, modalities)` to obtain $z$.

- **Keyword Matching & Coverage**:
  - Normalization: extract alphanumeric words, lowercase, filter standard English/code stopwords.
  - FTS5 query: execute `db.fts_search(clean_query, limit=200)` to retrieve BM25 ranked matches and snippets.
  - Coverage: $\text{coverage} = \frac{\text{count}(\text{matched non-stopwords in snippet/text})}{\max(\text{count}(\text{query non-stopwords}), 1)} \in [0.0, 1.0]$.

- **DigUp Ranking Formula & Bonuses**:
  $$\text{Score} = W_{\text{meaning}} \cdot \max(z, 0) + W_{\text{words}} \cdot (\text{coverage})^{1.5} + \text{HintBonus}$$
  - $\text{HintBonus}$:
    - Filename match bonus: $+0.5 \times \text{name\_coverage}$ if filename contains query terms.
    - Code definition match bonus: $+0.3$ if segment defines matched identifier (`focus > 0`).

- **Gating**:
  - Segment candidates MUST satisfy: $z \ge 2.0$ OR $\text{coverage} \ge 0.5$.
  - Candidates failing both criteria are pruned before aggregation.

- **Aggregation & Weaker Matches Folding**:
  - Group hits by `file_id`, selecting the highest-scoring segment hit per file.
  - Top score: $\text{topScore} = \max(\text{scores})$.
  - Results where $\text{score} < 0.5 \times \text{topScore}$ are classified as weaker matches (`weaker_hits`).
  - `format_results` returns `(primary_rows, weaker_rows)` as `ResultRow` instances.

### 2. Indexing Service Architecture (`src/semsearch/services/indexer.py`)

- **Class Contracts**:
  ```python
  class IndexQueueTier(Enum):
      NEW_FILES = "new_files"
      BACKFILL = "backfill"
      CODE = "code"
      LONG_FILES = "long_files"

  @dataclass
  class MeasuredCosts:
      ms_per_chunk: float = 40.0
      ms_per_image: float = 120.0
      ms_per_audio: float = 150.0
      bytes_per_segment: int = 1600

      def update(self, modality: Modality, elapsed_ms: float, count: int = 1) -> None: ...
      def estimate_time(self, pending_counts: dict[str, int]) -> float: ...
      def estimate_disk_growth(self, pending_count: int) -> int: ...

  class IndexService:
      def __init__(
          self,
          db: Database,
          code_db: Database | None = None,
          ollama: OllamaClient | None = None,
          config: AppConfig | None = None,
      ) -> None: ...

      def start(self) -> None: ...
      def stop(self) -> None: ...
      def pause(self, reason: str = "") -> None: ...
      def resume(self) -> None: ...
      def get_status(self) -> EngineStatus: ...
      def enqueue_file(self, path: str, tier: IndexQueueTier = IndexQueueTier.NEW_FILES) -> None: ...
      def process_file(self, file_record: FileRecord, target_db: Database) -> bool: ...
  ```

- **Two-Stage Pipelined Worker**:
  - Stage 1 (Prepare): Reads file content, executes content extractor (`images`, `pdfs`, `docs`, `media`, `code`), extracts segments, constructs text/base64 payloads, and dispatches batch embeddings to Ollama.
  - Stage 2 (Commit): Writes segments, vector blobs, and FTS records into SQLite in a single transaction, marks file state `DONE` (or `PARTIAL`), and updates counters.
  - Pipelining decouples model inference from database transactions, keeping WAL locks brief and allowing concurrent searches.

- **Priority Queue Tiers**:
  1. `NEW_FILES`: Immediate fast-track queue for newly detected files (up to 50 items).
  2. `BACKFILL`: Unindexed general documents, PDFs, and images from configured folders.
  3. `CODE`: Source code files for `code.sqlite`.
  4. `LONG_FILES`: Partial files continuation (e.g. PDFs pages 31+, long video/audio).

- **Partial File Continuation**:
  - PDFs with >30 pages index pages 1–30 in pass 1 and receive `state = FileState.PARTIAL` with `info = {"last_page": 30, "total_pages": N}`.
  - When tier `LONG_FILES` executes, extraction resumes from `start_page = last_page`. Upon reaching the final page, `state` is updated to `FileState.DONE`.

- **Dynamic Cost Tracking & Estimation**:
  - `MeasuredCosts` tracks moving averages of milliseconds per segment type and database bytes per segment.
  - Calculates estimated remaining seconds and disk space required for remaining pending files.

### 3. Filesystem Watcher Architecture (`src/semsearch/services/watcher.py`)

- **Class Contract**:
  ```python
  class FileSystemWatcher:
      def __init__(
          self,
          index_service: IndexService,
          config: AppConfig,
      ) -> None: ...

      def start(self) -> None: ...
      def stop(self) -> None: ...
  ```

- **Linux inotify with `watchdog`**:
  - `Observer` monitors configured folders recursively.
  - Differentiated debouncing:
    - General folders (`config.folders`): event latency 0.25s, quiet debounce window 0.75s.
    - Code folders (`config.code_folders`): event latency 2.0s, quiet debounce window 60s, maximum wait ceiling 600s (10 minutes).
  - Event Handling:
    - Created / Modified: File debounced and enqueued for indexing.
    - Deleted: File removed from database immediately via `db.delete_file`.
    - Moved: File path and FTS entry updated via `db.move_file`.
  - Filters: Hidden files (`.*`), excluded directories (`.git`, `node_modules`, `__pycache__`), excluded extensions (`heic`, etc.).

### 4. Scheduler & Power Policy Architecture (`src/semsearch/services/scheduler.py`)

- **Class Contract**:
  ```python
  class PowerScheduler:
      def __init__(
          self,
          index_service: IndexService,
          config: AppConfig,
          poll_interval_sec: float = 10.0,
      ) -> None: ...

      def is_on_battery(self) -> bool: ...
      def is_within_schedule(self) -> bool: ...
      def evaluate_policy(self) -> tuple[bool, str]: ...
      def start(self) -> None: ...
      def stop(self) -> None: ...
  ```

- **Native Linux Battery Inspection**:
  - Reads `/sys/class/power_supply/`:
    - Checks for power supply devices with type `Battery`.
    - Reads `/sys/class/power_supply/BAT*/status`.
    - If status is `"Discharging"`, `is_on_battery()` returns `True`.
  - If `config.index_on_battery` is `False` and battery is discharging, triggers `index_service.pause("AC power")`.

- **Operating Hours Schedule**:
  - Config parameters: `index_schedule_enabled` (bool), `index_schedule_start` (e.g. 1320 for 22:00), `index_schedule_end` (e.g. 420 for 07:00).
  - Overnight wrap handling: if `start > end`, active when `now >= start or now < end`. Otherwise active when `start <= now < end`.
  - If outside schedule, triggers `index_service.pause("Scheduled hours")`.

### 5. Desktop Action Handlers Architecture (`src/semsearch/services/actions.py`)

- **Signatures**:
  ```python
  def open_file_default(path: str) -> None: ...
  def open_media_timestamp(path: str, seconds: float) -> None: ...
  def open_pdf_page(path: str, page: int) -> None: ...
  def open_code_editor(path: str, line: int, editor_preference: str = "auto") -> None: ...
  def reveal_in_file_manager(path: str) -> None: ...
  def copy_to_clipboard(text: str) -> None: ...
  def execute_hit_action(hit: SearchHit, config: AppConfig | None = None) -> None: ...
  ```

- **Desktop Action Implementations**:
  - `open_file_default`: executes `xdg-open <path>`.
  - `open_media_timestamp`: checks for `mpv` (`["mpv", f"--start={int(seconds)}", path]`), then `vlc` (`["vlc", f"--start-time={int(seconds)}", path]`), falling back to `xdg-open`.
  - `open_pdf_page`: checks for `okular` (`["okular", "-p", str(page), path]`), then `evince` (`["evince", "-p", str(page), path]`), falling back to `xdg-open`.
  - `open_code_editor`: launches configured or detected editor:
    - VS Code: `["code", "-g", f"{path}:{line}"]`
    - Cursor: `["cursor", "-g", f"{path}:{line}"]`
    - Zed: `["zed", f"{path}:{line}"]`
    - Sublime Text: `["subl", f"{path}:{line}"]`
    - JetBrains (`pycharm`, `idea`, `clion`): `[bin, "--line", str(line), path]`
  - `reveal_in_file_manager`: attempts DBus call `org.freedesktop.FileManager1.ShowItems` passing URI `file://{path}`. Falls back to CLI commands (`dolphin --select`, `nautilus --select`, `nemo --select`), and lastly `xdg-open` on the parent directory.
  - `copy_to_clipboard`: copies text to system clipboard via `wl-copy`, `xclip`, or PySide6 `QClipboard`.

## Risks / Trade-offs

- **SQLite WAL contention during concurrent search and indexing**: Frequent small write commits could block search readers. → Mitigated by the two-stage pipelined worker: extraction and embedding happen asynchronously in batch memory, and commits are executed in coalesced transactions with `PRAGMA busy_timeout = 5000`.
- **Inotify event flooding during git checkouts or builds**: Rapid modifications could overload the indexing pipeline. → Mitigated by code folder debouncing with a 60-second quiet window and a 10-minute maximum ceiling.
- **Ollama inference latency impacting search responsiveness**: Embedding search queries can take 50–100ms. → Query embeddings use small batches of 1 item with explicit timeouts, falling back to pure FTS5 BM25 search if Ollama is unreachable.
- **Battery drain on laptops during heavy indexing**: Batch multimodal embedding is compute-intensive. → Native Linux power supply polling pauses indexing automatically when on battery if configured.
