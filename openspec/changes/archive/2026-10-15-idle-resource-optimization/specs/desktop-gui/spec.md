# desktop-gui Specification

## MODIFIED Requirements

### Requirement: System Tray Monitor with Dynamic Progress Ring
The application MUST maintain an icon in the Linux system tray. While indexing is in progress, the tray icon MUST poll `index_service.get_status()` to render a circular progress ring reflecting live completion percentage. The tray manager MUST cache `_last_rendered_state` and skip icon painting, tooltip updates, and D-Bus updates when the polled state is unchanged. The tray icon MUST provide a context menu to toggle indexing pause/resume, open search panel or window, open settings, and quit.

#### Scenario: Visual progress ring during indexing
- **WHEN** the indexing service is actively processing files
- **THEN** the tray icon polls live status counts and displays a circular progress ring arc corresponding to the indexed fraction

#### Scenario: Idle icon state when indexing completes
- **WHEN** the indexing queue becomes empty and the engine transitions to idle
- **THEN** the tray icon reverts to the static application brand emblem

#### Scenario: Pausing indexing from tray context menu
- **WHEN** the user selects the "Pause Indexing" action in the tray context menu
- **THEN** active indexing pauses immediately and the context menu item text updates to "Resume Indexing"

#### Scenario: Suppressing redundant tray rendering and D-Bus updates when status is unchanged
- **WHEN** `update_status()` is called repeatedly with an unchanged `EngineStatus` while waiting in the system tray
- **THEN** `SystemTrayManager` skips `render_tray_icon()`, `setIcon()`, `setToolTip()`, and menu action text updates until the state changes or `retranslate_ui()` invalidates `_last_rendered_state`

## ADDED Requirements

### Requirement: Zero-Resource System Tray Idle Operation
When the application is waiting in the system tray with no active indexing or search operations, it MUST NOT consume background CPU cycles, thread wakeups, SQLite queries, D-Bus updates, or retained UI preview memory unless strictly necessary.

#### Scenario: Application idling in the system tray
- **WHEN** both search views are hidden/closed and background indexing and filesystem debounce queues are empty
- **THEN** worker threads remain blocked on events, no SQLite queries or D-Bus icon updates are emitted on unchanged tray ticks, and preview/result memory buffers are released

### Requirement: Multimodal Preview Resource Release on Clear
When `PreviewPane.clear()` is invoked, it MUST switch to the empty view and invoke `clear()` on `ImagePreviewWidget`, `PdfPreviewWidget`, `VideoPreviewWidget`, `AudioPreviewWidget`, and `CodePreviewWidget` to close open `pymupdf.Document` instances and release retained `QPixmap` buffers and editor text.

#### Scenario: Releasing open PDF documents and pixmaps on preview clear
- **WHEN** `PreviewPane.clear()` is called after previewing PDFs, images, videos, audio, or code files
- **THEN** `PdfPreviewWidget` closes and clears `_doc` and `_rendered_pixmap`, `ImagePreviewWidget` and `VideoPreviewWidget` clear their `QPixmap` buffers, `AudioPreviewWidget` resets its path state, and `CodePreviewWidget` clears its editor text and extra selections
