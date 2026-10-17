# Spec Delta

## MODIFIED Requirements

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
