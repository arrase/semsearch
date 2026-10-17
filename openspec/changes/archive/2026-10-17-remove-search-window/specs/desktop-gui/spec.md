# Spec Delta

## MODIFIED Requirements

### Requirement: Single Instance Application Lock and IPC Invocation
The desktop GUI MUST enforce a single running instance per user session via a local domain socket server. Launching `semsearch` without flags or with `--daemon` MUST anchor the application in the system tray and start background services without opening the quick search panel (unless onboarding is incomplete, which runs the wizard first), while `--panel` opens the search panel and `--settings` opens settings. Secondary processes MUST forward their command over the socket and exit with code zero.

#### Scenario: Primary instance starts anchored in system tray without opening search panel
- **WHEN** `semsearch` is launched without flags on an already onboarded installation
- **THEN** the application displays the system tray icon, starts background indexing and the global hotkey listener, and keeps the quick search panel hidden until requested by the user

#### Scenario: Second instance activates running instance
- **WHEN** a user executes `semsearch --panel` or `semsearch --settings` while an instance is already running
- **THEN** the second instance transmits the command to the primary instance over the local socket and exits immediately

#### Scenario: Primary instance activates search panel
- **WHEN** the primary instance receives an IPC invocation message requesting the search panel
- **THEN** the primary instance surfaces the quick search panel to the front and gives it keyboard focus

### Requirement: System Tray Monitor with Dynamic Progress Ring
The application MUST maintain an icon in the Linux system tray. While indexing is in progress, the tray icon MUST poll `index_service.get_status()` to render a circular progress ring reflecting live completion percentage. The tray manager MUST cache `_last_rendered_state` and skip icon painting, tooltip updates, and D-Bus updates when the polled state is unchanged. The tray icon MUST provide a context menu to toggle indexing pause/resume, open the quick search panel, open settings, and quit.

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

### Requirement: Zero-Resource System Tray Idle Operation
When the application is waiting in the system tray with no active indexing or search operations, it MUST NOT consume background CPU cycles, thread wakeups, SQLite queries, D-Bus updates, or retained UI preview memory unless strictly necessary.

#### Scenario: Application idling in the system tray
- **WHEN** the quick search panel is hidden and background indexing and filesystem debounce queues are empty
- **THEN** worker threads remain blocked on events, no SQLite queries or D-Bus icon updates are emitted on unchanged tray ticks, and preview/result memory buffers are released
