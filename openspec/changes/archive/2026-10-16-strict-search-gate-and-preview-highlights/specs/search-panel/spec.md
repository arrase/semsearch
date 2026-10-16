# Spec Delta

## MODIFIED Requirements

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
