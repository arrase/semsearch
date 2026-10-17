# Spec Delta

## MODIFIED Requirements

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
