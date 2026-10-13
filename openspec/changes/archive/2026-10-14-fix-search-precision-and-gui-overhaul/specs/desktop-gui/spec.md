# desktop-gui Specification Delta

## MODIFIED Requirements

### Requirement: Single Instance Application Lock and IPC Invocation
The desktop GUI MUST enforce a single running instance per user session using a local domain socket server. Launching `semsearch` without command-line mode flags MUST default to opening the main search window (`SHOW_WINDOW`), while `--daemon` runs in the background and `--panel` opens the quick search panel. When a secondary process is launched, it MUST send an invocation message to the running instance and exit with return code zero, and the running instance MUST bring the requested window or panel to the foreground.

#### Scenario: Second instance activates running instance
- **WHEN** a user executes `semsearch` or `semsearch --window` while an instance is already running
- **THEN** the second instance transmits the window show command to the primary instance over the local socket and exits immediately

#### Scenario: Primary instance activates search panel
- **WHEN** the primary instance receives an IPC invocation message requesting the search panel
- **THEN** the primary instance surfaces the quick search panel to the front and gives it keyboard focus

### Requirement: System Tray Monitor with Dynamic Progress Ring
The application MUST maintain an icon in the Linux system tray. While indexing operations are in progress, the tray icon MUST poll `index_service.get_status()` to dynamically render an animated circular progress ring using vector painting reflecting live percentage completion. The tray icon MUST provide a context menu with options to toggle indexing pause/resume, open search panel, open full window, open settings, and quit.

#### Scenario: Visual progress ring during indexing
- **WHEN** the indexing service is actively processing files
- **THEN** the tray icon polls live status counts and displays a circular progress ring arc corresponding to the indexed fraction

#### Scenario: Idle icon state when indexing completes
- **WHEN** the indexing queue becomes empty and the engine transitions to idle
- **THEN** the tray icon reverts to the static application brand emblem

#### Scenario: Pausing indexing from tray context menu
- **WHEN** the user selects the "Pause Indexing" action in the tray context menu
- **THEN** active indexing pauses immediately and the context menu item text updates to "Resume Indexing"

### Requirement: Multi-Tab Settings Configuration
The settings interface MUST provide a dialog with four dedicated tabs: Folders, Code, Storage & Model, and General, with properly sized list views and spinbox controls. Saving settings MUST emit a notification that immediately updates the running application's configuration, file watcher descriptors, hotkeys, Ollama model settings, and background indexing queues.

#### Scenario: Restricting model selection to multimodal embedding models
- **WHEN** the user inspects the model dropdown in the Storage & Model tab
- **THEN** the dropdown displays only models capable of multimodal embeddings and preserves the user's configured model selection

#### Scenario: Selecting code editor preference
- **WHEN** the user selects an editor in the Code settings tab
- **THEN** the preference is saved and subsequent code search jumps invoke the selected editor

#### Scenario: Modifying searched directories
- **WHEN** the user adds or removes a folder or exclusion in the Folders tab and saves
- **THEN** the configuration file is updated, the file watcher updates its watch descriptors, and the indexing service re-synchronizes
