# search-window Specification

## Purpose
Provides an expansive standalone desktop search window with multimodal kind filtering, responsive card grid views, dedicated code syntax listings, an integrated preview sidebar, and native drag-and-drop export.

## Requirements

### Requirement: Standalone Window Geometry and Layout Architecture
The search window MUST provide a standalone resizable window initialized to 1040x700 dimensions with a minimum constraint of 800x500. The layout MUST feature a top search header with kind chip selectors, a central content area displaying results as an adaptive card grid or syntax list, and a right-aligned preview sidebar.

#### Scenario: Initial window presentation and dimensioning
- **WHEN** the search window is launched
- **THEN** it renders with dimensions of 1040x700 pixels containing the search input, kind chip bar, central results area, and preview sidebar

#### Scenario: Window resizing responsiveness
- **WHEN** the user resizes the search window
- **THEN** the central content area flexes dynamically while the preview sidebar and kind chips preserve functional layouts

### Requirement: Modality Kind Chips and Shortcut Navigation
The search window MUST provide kind chip filters for All, PDF, Images, Audio, Video, Code, and Docs. Each kind chip MUST be mapped to global window shortcuts Ctrl+1 through Ctrl+7 respectively. Selecting a kind chip MUST filter the active search results to the selected modality.

#### Scenario: Filtering results by kind chip click
- **WHEN** the user clicks on the "Images" kind chip
- **THEN** only image results are shown in the results area and the Images chip is highlighted as active

#### Scenario: Switching kind filter via keyboard shortcut
- **WHEN** the user presses Ctrl+2 anywhere within the search window
- **THEN** the filter immediately switches to "PDF" documents and updates the result view

#### Scenario: Resetting to all modalities
- **WHEN** the user presses Ctrl+1 or clicks "All"
- **THEN** results from all indexed modalities are displayed together

### Requirement: Adaptive Card Grid View
The search window MUST present visual and document results within an adaptive multi-column card grid inside a scrolling container. Each card MUST render a visual thumbnail preview, file name, matched text snippet, modification timestamp or size, and relevance badge.

#### Scenario: Displaying image and document cards
- **WHEN** search results contain images or PDF documents in the grid view
- **THEN** each result renders as a distinct card with thumbnail, title, snippet, and metadata

#### Scenario: Card selection synchronizes preview sidebar
- **WHEN** a user selects a card in the grid view
- **THEN** the right preview sidebar displays the detailed multimodal preview of the selected file

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
