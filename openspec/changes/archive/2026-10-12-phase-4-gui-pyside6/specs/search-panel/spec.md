# Spec Delta

## Purpose

Provides a fast, borderless, keyboard-driven floating quick search panel with instant lexical search, debounced semantic ranking, two-column results, and synchronized multimodal previews.

## ADDED Requirements

### Requirement: Floating Borderless Geometry and Dynamic Expansion
The quick search panel MUST render as a frameless, borderless floating window with 18px rounded corners centered in the upper third of the active screen. The panel MUST present a collapsed height of 56pt containing only the search input bar when empty, and MUST expand vertically to 480pt to display a two-column view (330px result list and 418px preview pane) when search results are available.

#### Scenario: Displaying collapsed input bar on invocation
- **WHEN** the search panel is invoked without an active search query
- **THEN** it displays centered with an 18px rounded borderless frame at a collapsed height of 56pt focusing the search input

#### Scenario: Expanding panel when results are present
- **WHEN** a search query yields one or more matching results
- **THEN** the panel expands its height to 480pt and reveals the two-column 330px result list and 418px preview pane

#### Scenario: Collapsing panel when search input is cleared
- **WHEN** the user clears the search input text completely
- **THEN** the panel collapses back to 56pt height and hides the result list and preview pane

### Requirement: Dual-Cadence Search Execution
The search panel MUST execute an immediate full-text search against the SQLite FTS5 index upon keystroke input, and MUST trigger the hybrid semantic vector search after a 120ms debounce period. The search panel MUST update the displayed result list with hybrid ranked results when semantic fusion completes.

#### Scenario: Instant lexical search feedback
- **WHEN** the user types characters into the search input box
- **THEN** lexical FTS5 matches populate the result list immediately without waiting for embedding generation

#### Scenario: Debounced semantic search completion
- **WHEN** user typing pauses for at least 120 milliseconds
- **THEN** the semantic query embedding executes and results are fused using the DigUp hybrid ranking formula

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
The search panel MUST support complete keyboard navigation. Up and Down arrow keys MUST navigate between result items; Enter MUST open the selected item using its default desktop action; Shift+Enter or Ctrl+Enter MUST reveal the selected file in the system file manager; and Escape MUST clear the input if non-empty, or hide the panel if input is already empty.

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
The search panel MUST host a 418px wide preview pane that updates immediately whenever the highlighted result changes. The preview pane MUST render specialized preview widgets for images, PDF pages with matching terms highlighted in yellow, audio files with waveform bars and playback control, video files with seek scrub bars, and code snippets with line numbers and matched definition lines.

#### Scenario: Updating preview when selecting an image file
- **WHEN** an image result is highlighted in the result list
- **THEN** the preview pane renders the scaled image preserving its aspect ratio

#### Scenario: Updating preview when selecting a PDF file
- **WHEN** a PDF result is highlighted in the result list
- **THEN** the preview pane renders the matched page with query keyword occurrences highlighted in yellow

#### Scenario: Updating preview when selecting a code file
- **WHEN** a code search result is highlighted in the result list
- **THEN** the preview pane renders syntax-highlighted code lines with gutter line numbers and highlights the definition anchor line
