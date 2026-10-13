# search-panel Specification Delta

## MODIFIED Requirements

### Requirement: Floating Borderless Geometry and Dynamic Expansion
The quick search panel MUST render as a frameless, borderless floating window with 18px rounded corners centered in the upper third of the active screen. The panel MUST size its width and margins so that the search header, two-column body (result list + divider + preview pane), and shortcut footer fit without horizontal or vertical clipping when expanded.

#### Scenario: Displaying collapsed input bar on invocation
- **WHEN** the search panel is invoked without an active search query
- **THEN** it displays centered with an 18px rounded borderless frame at its collapsed height focusing the search input without clipping the input borders

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

### Requirement: Synchronized Inline Multimodal Preview
The search panel MUST host a synchronized preview pane that updates immediately whenever the highlighted result changes. The preview pane MUST render dynamically scaled images, PDF pages with matching terms highlighted in yellow, document text passages with matching terms highlighted, audio files with waveform bars and external player launch, video files with extracted frame thumbnails at the matched timestamp, code snippets with line numbers and matched definition lines, and `Open` / `Show in Files` buttons.

#### Scenario: Updating preview when selecting an image file
- **WHEN** an image result is highlighted in the result list
- **THEN** the preview pane renders the scaled image preserving its aspect ratio within the available viewport

#### Scenario: Updating preview when selecting a PDF file
- **WHEN** a PDF result is highlighted in the result list
- **THEN** the preview pane renders the matched page with query keyword occurrences highlighted in yellow

#### Scenario: Updating preview when selecting a code file
- **WHEN** a code search result is highlighted in the result list
- **THEN** the preview pane renders syntax-highlighted code lines with gutter line numbers and highlights the definition anchor line
