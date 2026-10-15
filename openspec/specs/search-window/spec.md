# search-window Specification

## Purpose
Provides an expansive standalone desktop search window with multimodal kind filtering, responsive card grid views, dedicated code syntax listings, an integrated preview sidebar, and native drag-and-drop export.

## Requirements

### Requirement: Standalone Window Geometry and Layout Architecture
The search window MUST provide a standalone resizable window initialized to 1040x700 (minimum 800x500). Its layout MUST feature a top search header with kind chips, a central area displaying a responsive multi-column card grid or code syntax list (with empty state, no-results feedback, and foldable weaker matches), a right multimodal preview sidebar with action buttons, and a bottom status bar.

#### Scenario: Initial window presentation and dimensioning
- **WHEN** the search window is launched
- **THEN** it renders with dimensions of 1040x700 pixels containing the search input, kind chip bar, central results area, preview sidebar, and bottom status bar

#### Scenario: Window resizing responsiveness
- **WHEN** the user resizes the search window or adjusts the splitter
- **THEN** the card grid dynamically recalculates its column count to fit the available viewport width without horizontal clipping

### Requirement: Modality Kind Chips and Shortcut Navigation
The search window MUST provide kind chip filters for All, PDF, Images, Screenshots, Audio, Video, Code, and Docs with live result count badges. Selecting a kind chip MUST filter the active search results to the selected modality (with Images matching image files and Screenshots matching screenshot files).

#### Scenario: Filtering results by kind chip click
- **WHEN** the user clicks on a kind chip such as "Images" or "Screenshots"
- **THEN** only results matching that modality are shown in the results area and the selected chip is highlighted as active

#### Scenario: Switching kind filter via keyboard shortcut
- **WHEN** the user presses Ctrl+2 anywhere within the search window
- **THEN** the filter immediately switches to the second kind chip and updates the result view

#### Scenario: Resetting to all modalities
- **WHEN** the user presses Ctrl+1 or clicks "All"
- **THEN** results from all indexed modalities are displayed together

### Requirement: Adaptive Card Grid View
The search window MUST present visual and document results within an adaptive multi-column card grid inside a scrolling container, separating weaker matches into a collapsible section. Each card MUST render a visual thumbnail preview (including rendered PDF page and video frame thumbnails), file name, place badge (`p. 1`, `0:25`), single-line elided snippet or folder path, modification timestamp or size, and visual selection state.

#### Scenario: Displaying image and document cards
- **WHEN** search results contain images, screenshots, videos, or PDF documents in the grid view
- **THEN** each result renders as a distinct card with thumbnail, title, place badge, snippet, and metadata

#### Scenario: Card selection synchronizes preview sidebar
- **WHEN** a user selects a card in the grid view
- **THEN** the card highlights with a visible active border and the right preview sidebar displays the detailed multimodal preview and action buttons for the selected file

### Requirement: Code Syntax List View
When filtering by Code or when viewing code results, the search window MUST display items in a structured syntax list view. Each code item MUST show the repository or relative file path, the matching function or symbol definition, and code lines with gutter line numbers.

#### Scenario: Displaying code search matches
- **WHEN** the user selects the Code filter chip (Ctrl+6)
- **THEN** results switch to the syntax list view displaying file paths, symbol signatures, and line snippets

#### Scenario: Double-clicking code result opens editor at line
- **WHEN** the user double-clicks or presses Enter on a code result
- **THEN** the file opens in the configured code editor jumped directly to the matching definition line

### Requirement: Drag and Drop Native File Export
The search window MUST support initiating desktop drag-and-drop operations from any result card or list row. The drag payload MUST provide standard desktop URI list data allowing files to be dragged directly into file managers, terminal emulators, or desktop workspaces.

#### Scenario: Dragging a file card to an external application
- **WHEN** the user clicks and drags a result card or list row outside the window
- **THEN** a standard desktop drag operation is initiated with the file's absolute path URI

#### Scenario: Canceling drag operation without side effects
- **WHEN** a drag gesture is aborted or released over an invalid drop target
- **THEN** the search window remains unaffected and keeps the file selected

### Requirement: Safe Asynchronous SearchWorker Lifecycle Management in Search Window
The search window MUST retain active `SearchWorker` (`QThread`) references in an `_active_workers` set until each thread emits `finished`, connecting `finished` to remove the worker reference and call `deleteLater()` so slow workers exceeding 500ms during cancellation are never garbage-collected while still running.

#### Scenario: Slow search worker outliving cancellation timeout in window
- **WHEN** a new search or window close stops an active `SearchWorker` whose background execution takes longer than 500ms to finish
- **THEN** the worker remains referenced in `_active_workers` until its `finished` signal fires, preventing premature Python garbage collection and `QThread` `SIGABRT` crashes

### Requirement: Resource and SQLite Memory Cleanup on Window Close
When `closeEvent()` closes the standalone search window back to the system tray, the window MUST clear the search input, clear all result cards, code list items, and preview buffers via `_clear_results()`, and invoke `shrink_memory()` on the search databases.

#### Scenario: Closing search window releases UI cards, previews, and database memory
- **WHEN** the user closes the standalone search window (`closeEvent`)
- **THEN** the window clears `search_input`, invokes `_clear_results()` to destroy result cards and clear `preview_pane`, and calls `shrink_memory()` on `db` and `code_db`
