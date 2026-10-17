# Spec Delta

## ADDED Requirements

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
