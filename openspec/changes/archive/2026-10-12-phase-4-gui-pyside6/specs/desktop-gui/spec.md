# Spec Delta

## Purpose

Provides desktop integration services including single-instance application locking, a dynamic system tray progress monitor, an onboarding wizard, multi-tab settings management, and global hotkey dispatching.

## ADDED Requirements

### Requirement: Single Instance Application Lock and IPC Invocation
The desktop GUI MUST enforce a single running instance per user session using a local domain socket server. When a secondary process is launched with command-line arguments, it MUST send an invocation message to the running instance and exit with return code zero, and the running instance MUST bring the requested window or panel to the foreground.

#### Scenario: Second instance activates running instance
- **WHEN** a user executes `semsearch --window` while an instance is already running
- **THEN** the second instance transmits the window show command to the primary instance over the local socket and exits immediately

#### Scenario: Primary instance activates search panel
- **WHEN** the primary instance receives an IPC invocation message requesting the search panel
- **THEN** the primary instance surfaces the quick search panel to the front and gives it keyboard focus

### Requirement: System Tray Monitor with Dynamic Progress Ring
The application MUST maintain an icon in the Linux system tray. While indexing operations are in progress, the tray icon MUST dynamically render an animated circular progress ring using vector painting reflecting percentage completion. The tray icon MUST provide a context menu with options to toggle indexing pause/resume, open search panel, open full window, open settings, and quit.

#### Scenario: Visual progress ring during indexing
- **WHEN** the indexing service is actively processing files
- **THEN** the tray icon displays a circular progress ring arc corresponding to the indexed fraction

#### Scenario: Idle icon state when indexing completes
- **WHEN** the indexing queue becomes empty and the engine transitions to idle
- **THEN** the tray icon reverts to the static application brand emblem

#### Scenario: Pausing indexing from tray context menu
- **WHEN** the user selects the "Pause Indexing" action in the tray context menu
- **THEN** active indexing pauses immediately and the context menu item text updates to "Resume Indexing"

### Requirement: Multi-Tab Settings Configuration
The settings interface MUST provide a dialog with four dedicated tabs: Folders, Code, Storage & Model, and General. The Storage & Model tab MUST connect to the configured Ollama host and restrict the model selection dropdown exclusively to verified multimodal embedding models, defaulting to `embeddinggemma-2`.

#### Scenario: Restricting model selection to multimodal embedding models
- **WHEN** the user inspects the model dropdown in the Storage & Model tab
- **THEN** the dropdown displays only models capable of multimodal embeddings and excludes text-only LLMs

#### Scenario: Selecting code editor preference
- **WHEN** the user selects an editor in the Code settings tab
- **THEN** the preference is saved and subsequent code search jumps invoke the selected editor

#### Scenario: Modifying searched directories
- **WHEN** the user adds or removes a folder in the Folders tab
- **THEN** the configuration file is updated and the file watcher updates its watch descriptors

### Requirement: Five-Step Initial Onboarding Wizard
On first application startup where configuration indicates onboarding is incomplete, the application MUST launch a modal five-step wizard guiding the user through Welcome, Directory Selection, Ollama Multimodal Model Setup, Code Indexing Setup, and Completion with shortcut guidance.

#### Scenario: Launching onboarding on fresh installation
- **WHEN** the application starts with `onboarded` set to false
- **THEN** the five-step modal onboarding wizard displays automatically before the main tray or panel appears

#### Scenario: Completing onboarding wizard
- **WHEN** the user finishes the fifth step of the onboarding wizard
- **THEN** the configuration is saved with `onboarded` set to true and initial directory crawling begins

### Requirement: Global Desktop Hotkey Dispatching
The application MUST capture the global shortcut Ctrl+Shift+Space across Linux X11 and Wayland sessions to toggle the visibility of the quick search panel.

#### Scenario: Toggling search panel via global hotkey
- **WHEN** the user presses Ctrl+Shift+Space anywhere in the desktop environment
- **THEN** the quick search panel appears centered with keyboard focus if hidden, or dismisses if currently active
