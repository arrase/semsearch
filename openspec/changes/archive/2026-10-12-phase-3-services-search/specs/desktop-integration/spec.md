# Spec Delta

## Purpose

Provides Linux inotify filesystem event monitoring, differential debouncing, power supply policy enforcement, operating hours scheduling, and desktop deep links.

## ADDED Requirements

### Requirement: Inotify Filesystem Event Monitoring and Debouncing
The system MUST monitor configured folders using Linux inotify with differentiated debouncing: a 0.75s debounce window for general document folders, and a 60s quiet window with a 10-minute maximum wait ceiling for code repositories.

#### Scenario: Debouncing rapid document edits in general folders
- **WHEN** a document file is modified multiple times in quick succession within general folders
- **THEN** re-indexing is delayed until 0.75 seconds of quiet time have elapsed without further modifications

#### Scenario: Debouncing continuous repository changes during development
- **WHEN** files inside a code repository are frequently saved during editing or compilation
- **THEN** indexing is deferred until 60 seconds of quiet time have elapsed

#### Scenario: Enforcing maximum wait ceiling on continuous code edits
- **WHEN** continuous modifications in a code repository exceed 10 minutes without a 60-second quiet period
- **THEN** the debounce timer expires and queued code changes are dispatched for indexing

### Requirement: Linux Power Supply Policy Enforcement
The system MUST inspect `/sys/class/power_supply/` to detect battery discharge status and pause background indexing operations when configured to conserve battery power.

#### Scenario: Automatic indexing pause upon battery discharge
- **WHEN** the system is discharging battery power and battery indexing is disabled in settings
- **THEN** background indexing is paused and engine status indicates waiting for AC power

#### Scenario: Resuming indexing upon AC adapter reconnection
- **WHEN** the system reconnects to AC line power while indexing was paused for battery conservation
- **THEN** background indexing resumes immediately

### Requirement: Operating Hours Indexing Schedule
The system MUST restrict background indexing to designated operating hours when schedule mode is enabled, supporting time ranges that span midnight into the following morning.

#### Scenario: Pausing indexing outside scheduled active hours
- **WHEN** the current system time falls outside the configured indexing time schedule
- **THEN** background indexing is paused and engine status indicates waiting for scheduled hours

#### Scenario: Resuming indexing within scheduled overnight window
- **WHEN** the current system time enters a configured overnight indexing schedule spanning past midnight
- **THEN** background indexing is unpaused and proceeds with queued work

### Requirement: Desktop Deep Link Navigation and Action Handlers
The system MUST execute desktop action handlers to launch files with default applications, jump to media timestamps, open PDF documents at specific pages, position code editors at target lines, and reveal files in file managers.

#### Scenario: Jumping to media playback timestamp
- **WHEN** a user activates a search hit from an audio or video file with a timestamp offset
- **THEN** the system launches a compatible media player positioned at that exact playback timestamp

#### Scenario: Opening PDF at specific page
- **WHEN** a user activates a search hit from a PDF file with a page number
- **THEN** the system launches a compatible PDF viewer positioned at that page

#### Scenario: Opening code editor at symbol definition line
- **WHEN** a user activates a search hit from a source code file with a definition line number
- **THEN** the configured or auto-detected code editor is launched opened directly at that line

#### Scenario: Revealing selected file in desktop file manager
- **WHEN** a user requests revealing a search hit in the desktop file manager
- **THEN** the system issues a DBus or CLI command to open the containing folder with the target file highlighted
