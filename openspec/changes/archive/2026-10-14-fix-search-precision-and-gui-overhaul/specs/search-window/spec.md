# search-window Specification Delta

## MODIFIED Requirements

### Requirement: Standalone Window Geometry and Layout Architecture
The search window MUST provide a standalone resizable window initialized to 1040x700 dimensions with a minimum constraint of 800x500. The layout MUST feature a top search header with kind chip selectors, a central content area displaying results as a responsive multi-column card grid or syntax list (with empty-query home state, no-results feedback, and foldable weaker matches), a right-aligned multimodal preview sidebar with file action buttons, and a bottom status bar showing indexing status and result counts.

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
