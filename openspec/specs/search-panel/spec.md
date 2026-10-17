# search-panel Specification

## Purpose
Provides a fast, borderless, keyboard-driven floating quick search panel with instant lexical search, debounced semantic ranking, two-column results, and synchronized multimodal previews.

## Requirements

### Requirement: Floating Borderless Geometry and Dynamic Expansion
The quick search panel MUST render as a frameless floating window with 18px rounded corners centered in the upper third of the active screen, styled with a fully opaque elevated dark surface and a prominent brand-colored border to contrast against dark desktop backgrounds without content bleed-through. The panel MUST size its width and margins so the search header, two-column body, and shortcut footer fit without horizontal or vertical clipping when expanded.

#### Scenario: Displaying collapsed input bar on invocation
- **WHEN** the search panel is invoked without an active search query
- **THEN** it displays centered with an 18px rounded frame, an opaque elevated surface background, a visible brand-accent border, and a high-contrast search prompt at its collapsed height focusing the search input without clipping or background bleed-through

#### Scenario: Expanding panel when results are present
- **WHEN** a search query yields one or more matching results
- **THEN** the panel expands vertically to reveal the two-column result list and preview pane plus the bottom status/shortcut bar without horizontal overflow

#### Scenario: Collapsing panel when search input is cleared
- **WHEN** the user clears the search input text completely
- **THEN** the panel collapses back to its compact height and hides the result list and preview pane

### Requirement: Dual-Cadence Search Execution
The search panel MUST execute an immediate deduplicated full-text search against the SQLite FTS5 index upon keystroke input, and MUST trigger the hybrid semantic vector search with prefix matching enabled (`prefix_last=True`) after a 120ms debounce period.

#### Scenario: Instant lexical search feedback
- **WHEN** the user types characters into the search input box
- **THEN** deduplicated lexical FTS5 file matches populate the result list immediately and clear cleanly when no matches exist

#### Scenario: Debounced semantic search completion
- **WHEN** user typing pauses for at least 120 milliseconds
- **THEN** the semantic query embedding executes with prefix matching on the trailing token and updates the result list with hybrid ranked results

### Requirement: Result List Partitioning and Weaker Matches Folding
The search panel result list MUST organize primary search results grouped under kind headers. The result list MUST isolate matches scoring below 50% of the top result score into a folded "Weaker Matches" section that toggles expansion on user activation.

#### Scenario: Rendering kind headers for distinct result types
- **WHEN** search results contain matches spanning multiple modalities (such as documents, images, and code)
- **THEN** results are visually grouped beneath distinct modal category headers

#### Scenario: Collapsing weaker matches below 50% score threshold
- **WHEN** search results include candidate hits scoring below half of the top hit's score
- **THEN** those items are hidden inside a folded row showing the count of weaker matches

#### Scenario: Expanding folded weaker matches
- **WHEN** the user activates the folded weaker matches toggle row
- **THEN** the weaker matches expand in place and become selectable in the result list navigation

### Requirement: Full Keyboard Navigation and Action Shortcuts
The search panel MUST support complete keyboard navigation. Up and Down arrow keys MUST navigate between result items; Enter MUST open the selected item using its default desktop action; Shift+Enter or Ctrl+Enter MUST reveal the selected file in the file manager; Ctrl+C MUST copy the file to the clipboard; and Escape MUST clear the input if non-empty, or hide the panel if empty. The shortcut footer MUST display `↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss`.

#### Scenario: Navigating items with arrow keys
- **WHEN** the user presses Down or Up arrow keys in the search input or list
- **THEN** the active highlight moves to the next or previous result row and updates the preview pane

#### Scenario: Opening selected item with Enter
- **WHEN** the user presses the Enter key while an item is highlighted
- **THEN** the item opens in its registered system default application and the panel closes

#### Scenario: Revealing selected file in file manager
- **WHEN** the user presses Shift+Enter or Ctrl+Enter on a highlighted result
- **THEN** the containing directory opens in the desktop file manager with the file selected

#### Scenario: Escaping from active search
- **WHEN** the user presses Escape while text is present in the search input
- **THEN** the input text is cleared and the panel collapses to 56pt without closing

#### Scenario: Dismissing panel when input is empty
- **WHEN** the user presses Escape while the search input is empty
- **THEN** the search panel window hides

### Requirement: Synchronized Inline Multimodal Preview
The search panel MUST host a synchronized preview pane that updates immediately when the highlighted result changes. It MUST render dynamically scaled images, PDF pages and document passages with highlighted query matches (without applying a full-width yellow line highlight when no explicit target line `line > 0` exists), audio waveform previews, extracted video frames at matched timestamps, syntax-highlighted code with line numbers and anchored lines, and `Open` / `Show in Files` buttons.

#### Scenario: Updating preview when selecting an image file
- **WHEN** an image result is highlighted in the result list
- **THEN** the preview pane renders the scaled image preserving its aspect ratio within the available viewport

#### Scenario: Updating preview when selecting a PDF file
- **WHEN** a PDF result is highlighted in the result list
- **THEN** the preview pane renders the matched page with query keyword occurrences highlighted in yellow

#### Scenario: Updating preview when selecting a code file
- **WHEN** a code search result with a positive target line (`line > 0`) is highlighted in the result list
- **THEN** the preview pane renders syntax-highlighted code lines with gutter line numbers and highlights the definition anchor line in yellow

#### Scenario: Updating preview when selecting a document or semantic text hit without target line
- **WHEN** a document result (`FileKind.DOC`, including `.md` and `.txt`) or a text hit with `line <= 0` is highlighted in the result list
- **THEN** the preview pane renders the document text with line wrapping enabled, highlights any literal query term occurrences, scrolls to the first matched term or chunk excerpt, and omits any full-width yellow line highlight bar

### Requirement: Safe Asynchronous SearchWorker Lifecycle Management
The search panel MUST retain active `SearchWorker` (`QThread`) references in an `_active_workers` set until each thread emits `finished`, connecting `finished` to remove the worker reference and call `deleteLater()` so slow workers exceeding 500ms during cancellation are never garbage-collected while still running.

#### Scenario: Slow search worker outliving cancellation timeout
- **WHEN** a new search or panel dismissal stops an active `SearchWorker` whose background execution takes longer than 500ms to finish
- **THEN** the worker remains referenced in `_active_workers` until its `finished` signal fires, preventing premature Python garbage collection and `QThread` `SIGABRT` crashes

### Requirement: Resource and SQLite Memory Cleanup on Panel Dismissal
When `hide_panel()` dismisses the search panel back to the system tray, the panel MUST stop active search workers, clear result list widgets and `_current_result_set`, clear the `PreviewPane` to release preview buffers, collapse its geometry, and invoke `shrink_memory()` on the search databases.

#### Scenario: Dismissing search panel releases UI and database memory
- **WHEN** `hide_panel()` is called to hide the quick search panel
- **THEN** the panel clears `result_list`, resets `_current_result_set`, calls `preview_pane.clear()`, collapses to its compact height, and calls `shrink_memory()` on `db` and `code_db`

### Requirement: Automatic Panel Dismissal on Window Focus Loss and Preview Actions
The quick search panel MUST automatically dismiss itself when the panel window loses active window status to another window or desktop element, or when the user activates the Open or Show in Files buttons in the preview pane. Moving focus between child widgets within the search panel MUST NOT dismiss the panel.

#### Scenario: Dismissing panel when external window takes focus
- **WHEN** the quick search panel is visible and another window or desktop element takes focus
- **THEN** the search panel automatically hides and releases its UI and database buffers

#### Scenario: Preserving panel visibility on internal widget interaction
- **WHEN** the user clicks or moves focus to the result list or preview pane within the quick search panel
- **THEN** the search panel remains visible and active

#### Scenario: Dismissing panel when preview action buttons are clicked
- **WHEN** the user clicks the Open or Show in Files button inside the quick search panel's preview pane
- **THEN** the file action executes and the search panel automatically hides
