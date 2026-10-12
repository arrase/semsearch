# Design

## Context

Phases 1 through 3 established the SemSearch backend foundations: SQLite schema with FTS5 lexical indexing, vector math, Ollama client with multimodal embeddings, multimodal extractors (images, PDFs, documents, media, code) with zero OCR, hybrid DigUp ranking search service, two-stage pipelined indexing worker, inotify filesystem monitoring, power policy enforcement, and desktop deep links.

Phase 4 delivers the complete Linux desktop graphical user experience in PySide6 (Qt 6) replicating the sleek, keyboard-driven design of DigUp. The GUI consists of a floating Raycast-style quick search panel, an expansive standalone search window with faceted kind filters and card layouts, a dynamic system tray progress monitor, a four-tab settings dialog with strict multimodal model filtering, a five-step initial onboarding wizard, and a single-instance lock backed by a local domain socket.

## Goals / Non-Goals

**Goals:**
- Implement `theme.py` establishing the visual design system: translucent dark palette, brand gradients (`Brand.gradient`), leaf/amber accents, typography, and standardized QSS stylesheets.
- Implement `panel.py` providing `SearchPanel`: borderless floating window, 18px rounded corners, dynamic vertical expansion (56pt collapsed to 480pt expanded), two-column layout (330px list + 418px preview), instant FTS5 + 120ms debounced semantic search, kind headers, folded weaker matches row, and arrow/Enter/Escape keyboard navigation.
- Implement `preview.py` providing `PreviewPane` supporting specialized visual renderers for images, PDF pages with yellow keyword highlights, audio waveform bars with playback controls, video thumbnail scrubbers, and syntax-highlighted code with gutter line numbers and definition line highlights.
- Implement `window.py` providing `SearchWindow`: standalone 1040x700 window, kind chips (`Ctrl+1`..`Ctrl+7`), adaptive card grid, syntax list, preview sidebar, and drag & drop file export.
- Implement `tray.py` providing `SystemTrayManager`: dynamic `QPainter` circular progress ring icon during indexing, status tooltip, and context menu with pause/resume controls.
- Implement `settings.py` providing `SettingsDialog`: 4-tab dialog (Folders, Code, Storage & Model with verified multimodal Ollama models dropdown, General).
- Implement `onboarding.py` providing `OnboardingWizard`: 5-step modal workflow on fresh launch.
- Implement `hotkey.py` providing `HotkeyListener`: `Ctrl+Shift+Space` listener via `pynput` emitting thread-safe Qt signals.
- Implement `app.py` providing `SemSearchApp`: `QLocalServer`/`QLocalSocket` single-instance lock and IPC activation, service coordination, and widget lifecycle management.
- Update `main.py` CLI dispatcher supporting `--panel`, `--window`, `--settings`, `--daemon`.

**Non-Goals:**
- Supporting non-Linux desktop platforms (native Linux desktop integration only).
- Optical character recognition (strict zero OCR policy).
- Web-based or electron-based UI components (pure PySide6 Qt 6 native widgets only).
- Remote or cloud embedding models (local Ollama instance only).

## Decisions

### 1. Visual Design System (`src/semsearch/gui/theme.py`)

- **Palette & Tokens**:
  - Background surface: Translucent dark `rgba(22, 24, 29, 0.94)` (`#16181D`).
  - Card & input surface: `rgba(32, 35, 43, 0.85)` (`#20232B`).
  - Borders & separators: `rgba(255, 255, 255, 0.08)` (`#2E323D`).
  - Text colors: Primary `#F3F4F6`, Secondary `#9CA3AF`, Muted `#6B7280`.
  - Brand accents: Linear gradient `#6366F1` to `#8B5CF6` (`Brand.gradient`), Amber `#F59E0B` (pause/caution), Leaf `#10B981` (active/success), Highlight Yellow `rgba(250, 204, 21, 0.35)` (PDF text and code line highlights).
  - Typography: UI font family `"Inter", "Noto Sans", "Ubuntu", sans-serif`; Monospace family `"JetBrains Mono", "Fira Code", monospace`.
- **Public API**:
  ```python
  class ThemeColors:
      BG_PANEL: str = "rgba(22, 24, 29, 0.94)"
      BG_CARD: str = "rgba(32, 35, 43, 0.85)"
      BORDER: str = "rgba(255, 255, 255, 0.08)"
      TEXT_PRIMARY: str = "#F3F4F6"
      TEXT_SECONDARY: str = "#9CA3AF"
      TEXT_MUTED: str = "#6B7280"
      BRAND_START: str = "#6366F1"
      BRAND_END: str = "#8B5CF6"
      LEAF: str = "#10B981"
      AMBER: str = "#F59E0B"
      HIGHLIGHT_YELLOW: str = "rgba(250, 204, 21, 0.35)"

  def build_qss() -> str: ...
  def apply_theme(widget: QWidget) -> None: ...
  ```

### 2. System Tray & Dynamic Progress Ring (`src/semsearch/gui/tray.py`)

- **Rendering**:
  - `QSystemTrayIcon` with custom dynamically painted `QPixmap` (64x64).
  - `render_tray_icon(percent: float, is_indexing: bool, is_paused: bool) -> QIcon`:
    - Transparent 64x64 canvas, anti-aliased `QPainter`.
    - If `is_indexing`: draws outer track ring `#2E323D`, draws sweeping arc clockwise from 90° using `BRAND_START` / `LEAF` pen (width 4.5px) spanning `-360.0 * (percent / 100.0)`.
    - If `is_paused`: draws amber paused ring indicator.
    - If idle: renders clean, static brand emblem.
- **Context Menu Actions**:
  - Header: Status text (`"Idle - 4,812 indexed"`, `"Indexing: 45% (120/267)"`, or `"Paused"`).
  - Action: "Quick Search" (brings `SearchPanel` to focus).
  - Action: "Search Window" (shows `SearchWindow`).
  - Action: "Pause Indexing" / "Resume Indexing" (toggles indexer).
  - Separator.
  - Action: "Settings..." (opens `SettingsDialog`).
  - Action: "Quit" (shuts down services and terminates application).
- **Public API**:
  ```python
  class SystemTrayManager(QObject):
      def __init__(self, app_config: AppConfig, index_service: IndexService | None = None) -> None: ...
      def update_status(self, status: EngineStatus) -> None: ...
      def show(self) -> None: ...
      def hide(self) -> None: ...
  ```

### 3. Quick Search Panel (`src/semsearch/gui/panel.py`)

- **Window Geometry & Visuals**:
  - `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint`.
  - `Qt.WidgetAttribute.WA_TranslucentBackground`.
  - Border radius: 18px rounded corners via CSS and painter mask.
  - Dimensions:
    - Width: 748px fixed.
    - Height: 56px when empty (collapsed); 480px when containing results (expanded).
    - Centered horizontally in the upper third of the active desktop screen.
- **Layout Architecture**:
  - Top: `search_input` (`QLineEdit`, 56px height, 15px font, icon placeholder).
  - Bottom container (hidden when collapsed): horizontal split:
    - Left column: `ResultListView` (330px width, custom item delegate with kind icons, filename, relative path, score bar).
    - Right column: `PreviewPane` (418px width).
- **Dual-Cadence Search Pipeline**:
  - `textChanged` signal:
    - If text is empty: collapse panel height to 56px, clear results.
    - If text is non-empty:
      - 1. Execute immediate FTS5 query (`SearchService.search(query, limit=50)`) with lexical weighting, immediately display initial matches, expand panel to 480px.
      - 2. Restart 120ms single-shot `QTimer` (`self._semantic_timer`).
      - 3. When timer fires: dispatch query embedding and vector search to background thread, blend using DigUp hybrid ranking, update result model in-place.
- **Result Partitioning & Weaker Matches**:
  - Results grouped beneath kind category headers (`Documents`, `Media`, `Code`).
  - Hits with $\text{score} < 0.5 \times \text{topScore}$ are grouped in a folded row `"Weaker Matches (N matches)"`.
  - Clicking or pressing Enter on the weaker matches row unfolds it in-place.
- **Keyboard Navigation**:
  - `Up` / `Down`: traverses selectable items in result list.
  - `Enter`: activates item default action (`open_file_default` or `open_code_editor` at definition line).
  - `Shift+Enter` or `Ctrl+Enter`: activates `reveal_in_file_manager`.
  - `Escape`: clears search text if non-empty; hides panel if search text is empty.
- **Public API**:
  ```python
  class SearchPanel(QWidget):
      def __init__(self, search_service: SearchService, parent: QWidget | None = None) -> None: ...
      def show_panel(self) -> None: ...
      def hide_panel(self) -> None: ...
      def execute_search(self, query: str) -> None: ...
  ```

### 4. Multimodal Preview Component (`src/semsearch/gui/preview.py`)

- **View Stack (`QStackedWidget`)**:
  - Index 0: `EmptyPreviewWidget`: placeholder icon and prompt.
  - Index 1: `ImagePreviewWidget`:
    - Renders `QPixmap` preserving aspect ratio (`Qt.AspectRatioMode.KeepAspectRatio`).
    - Displays dimensions (e.g. `1920x1080`) and formatted byte size.
  - Index 2: `PdfPreviewWidget`:
    - Uses `fitz` (PyMuPDF) to render the matched page as a high-DPI `QPixmap`.
    - Searches page for matched query tokens; draws yellow highlight bounding boxes (`HIGHLIGHT_YELLOW`).
    - Page navigation buttons (`Prev`, `Next`, `Page X of Y`).
  - Index 3: `AudioPreviewWidget`:
    - Draws synthetic 64-bar waveform using `QPainter`.
    - Play/Pause toggle, playback slider, time elapsed / duration.
  - Index 4: `VideoPreviewWidget`:
    - Thumbnail poster extraction via `PyMuPDF` / `Pillow` or media cache.
    - Duration label, scrub slider, "Open in Player" timestamp jump button (`open_media_timestamp`).
  - Index 5: `CodePreviewWidget`:
    - Read-only `QPlainTextEdit` with monospace font.
    - `LineNumberGutter` rendering 1-indexed line numbers.
    - Highlights matched definition line (`anchor_line`) with a yellow background bar.
- **Public API**:
  ```python
  class PreviewPane(QStackedWidget):
      def __init__(self, parent: QWidget | None = None) -> None: ...
      def load_result(self, row: ResultRow, query: str = "") -> None: ...
      def clear(self) -> None: ...
  ```

### 5. Full Search Window (`src/semsearch/gui/window.py`)

- **Window Layout**:
  - Size: 1040x700 (minimum 800x500).
  - Top bar:
    - Search input field.
    - Kind chip filter bar: `All` (`Ctrl+1`), `PDF` (`Ctrl+2`), `Images` (`Ctrl+3`), `Audio` (`Ctrl+4`), `Video` (`Ctrl+5`), `Code` (`Ctrl+6`), `Docs` (`Ctrl+7`).
  - Central content: `QSplitter`:
    - Left/Center area (`QStackedWidget`):
      - `CardGridView`: `QScrollArea` containing flow grid of `ResultCard` widgets for visual/document results.
      - `CodeListView`: `QTableView` or `QListWidget` for structured code results with path, symbol signature, and line snippet.
    - Right area: full-height `PreviewPane`.
- **Drag & Drop Native File Export**:
  - `ResultCard` and list items implement `mousePressEvent` / `mouseMoveEvent`.
  - When dragging exceeds `QApplication.startDragDistance()`:
    - Constructs `QDrag`.
    - Sets `QMimeData.setUrls([QUrl.fromLocalFile(file_path)])`.
    - Executes `drag.exec_(Qt.DropAction.CopyAction)`.
- **Shortcuts**:
  - `QShortcut(QKeySequence("Ctrl+1"), self, ...)` through `Ctrl+7`.
  - `Ctrl+F`: focuses search input.
- **Public API**:
  ```python
  class SearchWindow(QMainWindow):
      def __init__(self, search_service: SearchService, parent: QWidget | None = None) -> None: ...
      def set_kind_filter(self, kind: str) -> None: ...
      def perform_search(self, query: str) -> None: ...
  ```

### 6. Settings Dialog & Model Filtering (`src/semsearch/gui/settings.py`)

- **Dialog Structure (`QTabWidget`)**:
  - **Tab 1: Folders**:
    - `QListWidget` listing `AppConfig.folders`.
    - Buttons: Add Folder (`QFileDialog.getExistingDirectory`), Remove Folder.
    - Excluded folders editor and excluded extensions editor (`heic`, `txt`, etc.).
  - **Tab 2: Code**:
    - Checkbox: "Enable Code Search" (`AppConfig.code_enabled`).
    - `QListWidget` for `AppConfig.code_folders`.
    - Editor picker `QComboBox`: `["Auto-detect", "VS Code", "Cursor", "Zed", "Sublime Text", "JetBrains"]`.
  - **Tab 3: Storage & Model**:
    - Ollama Host input (`AppConfig.ollama_url`).
    - "Test Connection" button calling `OllamaClient.list_models()`.
    - **Multimodal Model Selector**:
      - Populated strictly with verified multimodal embedding models.
      - Text-only models (`llama3`, `mistral`, `deepseek`, etc.) are filtered out.
      - Known multimodal models recognized: `embeddinggemma-2`, `nomic-embed-text-v1.5`, `clip`, `moondream`, `llava`, `minicpm-v`, `bge-m3`.
      - Defaults to `embeddinggemma-2`.
      - Displays badge indicating whether the model is already pulled or needs pulling.
    - Storage statistics: total indexed files, chunk count, database size on disk.
    - "Optimize / Vacuum Database" button.
  - **Tab 4: General**:
    - Global Hotkey input (`AppConfig.hotkey`, defaults to `Ctrl+Shift+Space`).
    - Autostart toggle: creates or removes `~/.config/autostart/semsearch.desktop`.
    - Battery policy checkbox ("Index on battery").
    - Operating hours schedule toggle with start/end time editors.
- **Public API**:
  ```python
  class SettingsDialog(QDialog):
      def __init__(self, config_manager: ConfigManager, ollama_client: OllamaClient, parent: QWidget | None = None) -> None: ...
      def load_settings(self) -> None: ...
      def save_settings(self) -> None: ...
  ```

### 7. Onboarding Wizard (`src/semsearch/gui/onboarding.py`)

- **5-Step Card Flow (`QWizard`)**:
  - Step 1: `WelcomePage` - Brand emblem, introduction to local private multimodal search.
  - Step 2: `FoldersPage` - Pre-selected standard folders (`~/Documents`, `~/Pictures`), browse button to add folders.
  - Step 3: `ModelPage` - Tests Ollama connection, detects or pulls `embeddinggemma-2`.
  - Step 4: `CodePage` - Prompts for developer mode, code roots selection, and preferred editor.
  - Step 5: `CompletionPage` - Displays global hotkey reminder (`Ctrl+Shift+Space`), tray icon info, and finish button.
- **Completion Hook**:
  - Updates `config.onboarded = True`, persists via `ConfigManager`, triggers initial directory index crawl.
- **Public API**:
  ```python
  class OnboardingWizard(QWizard):
      def __init__(self, config_manager: ConfigManager, ollama_client: OllamaClient, parent: QWidget | None = None) -> None: ...
  ```

### 8. Global Hotkey Interception (`src/semsearch/gui/hotkey.py`)

- **Implementation**:
  - Uses `pynput.keyboard.GlobalHotKeys` running in a dedicated background daemon thread.
  - Key combination parsed from config (`<ctrl>+<shift>+<space>`).
  - On trigger: emits `hotkey_triggered = Signal()` using Qt queued connection to ensure main thread safety.
- **Public API**:
  ```python
  class HotkeyListener(QObject):
      hotkey_triggered = Signal()
      def __init__(self, shortcut: str = "<ctrl>+<shift>+<space>") -> None: ...
      def start(self) -> None: ...
      def stop(self) -> None: ...
  ```

### 9. Application Shell & Single-Instance Lock (`src/semsearch/gui/app.py`)

- **Single Instance & IPC**:
  - Uses `QLocalServer` with server name `semsearch_instance_<uid>`.
  - On application startup:
    - Attempt `QLocalSocket.connectToServer(server_name)`.
    - If connection succeeds (another instance is running):
      - Write invocation command (`SHOW_PANEL`, `SHOW_WINDOW`, `SHOW_SETTINGS`) to socket and disconnect.
      - Terminate current process immediately.
    - If connection fails:
      - Call `QLocalServer.removeServer(server_name)`.
      - Call `server.listen(server_name)`.
      - Connect `newConnection` signal to IPC handler.
- **Lifecycle Coordination**:
  - Manages instances of `ConfigManager`, `Database`, `SearchService`, `IndexService`, `FileSystemWatcher`, `PowerScheduler`.
  - If `not config.onboarded`: display `OnboardingWizard`.
  - Initializes `SystemTrayManager`, `SearchPanel`, `SearchWindow`, `SettingsDialog`, `HotkeyListener`.
  - Connects hotkey signal to `toggle_panel()`.
- **Public API**:
  ```python
  class SemSearchApp(QApplication):
      def __init__(self, argv: list[str]) -> None: ...
      def handle_cli_action(self, action: str) -> None: ...
      def toggle_panel(self) -> None: ...
      def show_window(self) -> None: ...
      def show_settings(self) -> None: ...
      def run(self) -> int: ...
  ```

### 10. CLI Dispatcher (`src/semsearch/main.py`)

- **Dispatcher Logic**:
  - Parses arguments:
    - `--panel`: request quick search panel.
    - `--window`: request standalone search window.
    - `--settings`: request settings dialog.
    - `--daemon`: run headless in system tray.
  - Initializes `SemSearchApp(sys.argv)`.

## Risks / Trade-offs

- **[Risk]** Wayland compositor shortcut interception restrictions: Wayland compositors may restrict background `pynput` input capture.
  - **Mitigation:** The application supports IPC CLI activation (`semsearch --panel`, `semsearch --window`). Users or desktop environments (GNOME, KDE) can bind native system hotkeys directly to `semsearch --panel`.
- **[Risk]** Large image and PDF preview memory consumption: High-resolution scans could cause memory surges.
  - **Mitigation:** Previews are rendered on-demand scaled to screen DPI via `PyMuPDF` pixmaps, with image caching and instant cleanup on selection change.
- **[Risk]** Main UI thread freeze during vector semantic search: Generating embeddings via Ollama can take 100-300ms.
  - **Mitigation:** Instant lexical FTS5 search updates the UI immediately. Semantic vector search is debounced by 120ms and executed asynchronously in background worker threads, posting results back to the Qt event loop via signals.
- **[Risk]** Stale local socket lock files after system crash: Stale socket files could prevent startup.
  - **Mitigation:** `SemSearchApp` first attempts a socket connection. If connection fails or times out, `QLocalServer.removeServer()` safely clears stale domain sockets before listening.
