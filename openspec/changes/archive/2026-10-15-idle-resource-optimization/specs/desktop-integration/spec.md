# desktop-integration Specification

## MODIFIED Requirements

### Requirement: Inotify Filesystem Event Monitoring and Debouncing
The system MUST monitor configured folders using Linux inotify with differentiated debouncing: a 0.75s debounce window for general document folders, and a 60s quiet window with a 10-minute maximum wait ceiling for code repositories. When all debounce buckets are empty, the debounce worker thread MUST block on an event signal (`_wake_event`) with zero periodic CPU wakeups, polling at `check_interval_sec` only while at least one bucket holds pending paths.

#### Scenario: Debouncing rapid document edits in general folders
- **WHEN** a document file is modified multiple times in quick succession within general folders
- **THEN** re-indexing is delayed until 0.75 seconds of quiet time have elapsed without further modifications

#### Scenario: Debouncing continuous repository changes during development
- **WHEN** files inside a code repository are frequently saved during editing or compilation
- **THEN** indexing is deferred until 60 seconds of quiet time have elapsed

#### Scenario: Enforcing maximum wait ceiling on continuous code edits
- **WHEN** continuous modifications in a code repository exceed 10 minutes without a 60-second quiet period
- **THEN** the debounce timer expires and queued code changes are dispatched for indexing

#### Scenario: Zero-wakeup idle waiting when debounce buckets are empty
- **WHEN** no filesystem events are pending in any `DebounceBucket`
- **THEN** the watcher's `_debounce_loop` blocks indefinitely on `_wake_event` without periodic timer wakeups until an inotify event adds a path to a bucket or the watcher is stopped
