# Spec Delta

## MODIFIED Requirements

### Requirement: CLI Activation and IPC Message Dispatch
The command-line interface SHALL support flags to trigger the quick search panel, settings dialog, and background daemon mode, while defaulting to background system tray daemon mode when invoked without flags. Invocations of the CLI while an instance is already running SHALL forward the requested action to the active instance via local inter-process communication and terminate the invoking process immediately.

#### Scenario: Dispatching quick search panel from command line
- **WHEN** the command-line interface is launched with the panel flag
- **THEN** the floating quick search panel is displayed and focused

#### Scenario: Dispatching full search window from command line
- **WHEN** the command-line interface is launched without flags or with the daemon flag
- **THEN** the application starts anchored in the system tray without opening a search window or panel

#### Scenario: Dispatching settings dialog from command line
- **WHEN** the command-line interface is launched with the settings flag
- **THEN** the preferences settings dialog is displayed and focused

#### Scenario: Forwarding CLI command to existing running instance
- **WHEN** a second CLI process is launched while an instance is already active
- **THEN** the command is forwarded over local socket communication to the active instance and the secondary process exits
