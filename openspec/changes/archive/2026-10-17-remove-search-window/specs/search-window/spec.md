# Spec Delta

## REMOVED Requirements

### Requirement: Standalone Window Geometry and Layout Architecture
**Reason**: The standalone "Search Window" mode is being removed to consolidate exclusively on the floating Quick Search panel.
**Migration**: Use the floating Quick Search panel summoned via the global hotkey (`Ctrl+Shift+Space` by default) or `semsearch --panel`.

### Requirement: Modality Kind Chips and Shortcut Navigation
**Reason**: Modality kind chips were part of the removed standalone Search Window interface.
**Migration**: Results in the Quick Search panel are automatically grouped under modality headers, and source code can be searched with the `code:` prefix.

### Requirement: Adaptive Card Grid View
**Reason**: The multi-column card grid view existed only in the removed standalone Search Window.
**Migration**: Use the Quick Search panel's two-column result list and synchronized multimodal preview pane.

### Requirement: Code Syntax List View
**Reason**: The standalone window code list view is removed along with `SearchWindow`.
**Migration**: Prefix queries with `code:` in the Quick Search panel to view code matches and syntax-highlighted previews with line numbers.

### Requirement: Drag and Drop Native File Export
**Reason**: Result card drag-and-drop was specific to the standalone Search Window's card grid and code list views.
**Migration**: Use `Shift+Enter` (`Show in Files`) or `Ctrl+C` (`Copy`) in the Quick Search panel.

### Requirement: Safe Asynchronous SearchWorker Lifecycle Management in Search Window
**Reason**: `SearchWindow` is removed from the codebase.
**Migration**: Asynchronous `SearchWorker` lifecycle management remains enforced in `SearchPanel` (`search-panel` specification).

### Requirement: Resource and SQLite Memory Cleanup on Window Close
**Reason**: `SearchWindow` is removed from the codebase.
**Migration**: Resource and SQLite memory cleanup (`shrink_memory()`) remains enforced when `SearchPanel` is dismissed (`search-panel` specification).
