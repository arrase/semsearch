# desktop-gui Specification

## Purpose
Provides desktop application lifecycle services including secure single-instance locking and IPC, hybrid Wayland/X11 global hotkey dispatching, event-driven system tray progress monitoring, XDG-aware onboarding, reusable non-mutating settings widgets, clean resource shutdown, and lazy-loaded JSON internationalization.

## Requirements

### Requirement: Secure Single-Instance Application Lock and AF_UNIX IPC
`SemSearchApp` MUST register its desktop identity (`setDesktopFileName("semsearch")` and application SVG `QIcon`) and enforce a single running instance per user session inside an owner-private runtime directory (`$XDG_RUNTIME_DIR/semsearch`, fallback `/tmp/semsearch_<uid>`, mode `0o700`) using an exclusive non-blocking advisory lockfile (`instance.lock`, `fcntl.flock(LOCK_EX | LOCK_NB)`, mode `0o600`) and a local `AF_UNIX` socket (`ipc.sock`, `QLocalServer.SocketOption.UserAccessOption`, mode `0o600`). Secondary CLI invocations MUST forward commands (`TOGGLE`, `PANEL`, `SETTINGS`, `REINDEX`, `REINDEX_FORCE`, `DAEMON`) via Python `socket.AF_UNIX` (`forward_ipc_command()`) and exit with code `0`; the server MUST schedule accepted sockets for cleanup via `deleteLater()`.

#### Scenario: Preventing duplicate daemons and forwarding IPC commands
- **WHEN** `semsearch --toggle`, `semsearch --panel`, `semsearch --settings`, or `semsearch reindex` runs while a primary instance holds `instance.lock`
- **THEN** `forward_ipc_command()` sends the action over `ipc.sock` (`0o600`) and exits immediately, while the primary instance dispatches the action cleanly

### Requirement: Hybrid Global Hotkey Listener (Wayland XDG Portal + X11 pynput)
`HotkeyListener` MUST support both Wayland and X11 desktop sessions:
- On Wayland (`XDG_SESSION_TYPE=wayland` or `WAYLAND_DISPLAY` set), it MUST attempt registration via `PortalShortcutListener` using `org.freedesktop.portal.GlobalShortcuts` (`CreateSession`, `BindShortcuts`, `Activated` over `PySide6.QtDBus` with XDG trigger formatting such as `<Control><Shift>space`).
- On X11 (or when the portal is unavailable and `DISPLAY` is set), it MUST fall back to `pynput` `GlobalHotKeys` using `normalize_hotkey()` (mapping `super`/`meta`/`win`/`windows` -> `<cmd>`, `control` -> `<ctrl>`, `return` -> `<enter>`, `escape` -> `<esc>`).
- `HotkeyListener` MUST expose `backend_name` (`"portal"`, `"pynput"`, `"none"`), `last_error`, and `status_changed` displayed live in **Settings → General**.

#### Scenario: Wayland XDG GlobalShortcuts registration and X11 fallback
- **WHEN** `HotkeyListener.start()` runs under Wayland with an available portal, versus under X11
- **THEN** it binds via `org.freedesktop.portal.GlobalShortcuts` (`backend_name == "portal"`) on Wayland, falls back to `pynput` (`backend_name == "pynput"`) on X11, and reports any failure cleanly in Settings instead of silently ignoring errors

### Requirement: Event-Driven System Tray and No-Tray Fallback
`SystemTrayManager` MUST render HiDPI 128x128 (`devicePixelRatio = 2.0`) dynamic tray icons, cache `_last_rendered_state` to skip redundant repaints, stop `_poll_timer` when `EngineActivity.IDLE` (not paused) for zero idle polling, and resume polling immediately when `IndexService.add_work_listener` signals `_work_arrived` from background threads. When `QSystemTrayIcon.isSystemTrayAvailable()` is `False` and `--daemon` was not passed, `SemSearchApp.start()` MUST display the Quick Search Panel directly so the application remains usable on minimal window managers.

#### Scenario: Zero polling in IDLE and automatic wakeup on new work
- **WHEN** `IndexService` transitions to `IDLE` and later receives a new file via `enqueue_file()`
- **THEN** `SystemTrayManager` stops `_poll_timer` while `IDLE` and restarts it immediately upon receiving `_work_arrived`

#### Scenario: Desktop session without system tray support
- **WHEN** `SemSearchApp.start(daemon_mode=False)` launches on an onboarded environment where `QSystemTrayIcon.isSystemTrayAvailable()` is `False`
- **THEN** the Quick Search Panel opens automatically instead of hiding invisibly

### Requirement: XDG Onboarding Wizard and Clean Cancellation
On first startup (`onboarded == False`), `OnboardingWizard` MUST discover existing XDG standard directories (`Documents`, `Pictures`, `Desktop`, `Downloads`) via `QStandardPaths.writableLocation(...)`, skipping missing or duplicate home-equivalent paths. If the user cancels or closes the onboarding wizard without accepting, `SemSearchApp.start()` MUST call `shutdown()` and exit cleanly without leaving a background process.

#### Scenario: Discovering localized XDG directories and cancelling onboarding cleanly
- **WHEN** the onboarding wizard launches on a localized Linux desktop (e.g., `~/Documentos`, `~/Imágenes`) or the user cancels the wizard
- **THEN** `discover_standard_folders()` presents only existing XDG directories, and cancelling terminates the application cleanly via `shutdown()`

### Requirement: Reusable Non-Mutating Settings Widgets and Live Reconfiguration
`OnboardingWizard` and `SettingsDialog` MUST share `FolderListEditor` and `OllamaModelPicker` (`gui/widgets.py`). `OllamaModelPicker` MUST test connections asynchronously (`OllamaConnectionWorker`) with a temporary closed `OllamaClient`, NEVER mutating the active shared `OllamaClient` before clicking **Save**, NEVER displaying a false `"✓ Connected"` claim upon opening Settings, and preserving the configured model selection. Saving Settings MUST apply language/RTL direction, `code_enabled` toggling (opening or closing `code.sqlite`), `ollama.base_url`/`num_ctx`, `search_service.model`, model fingerprint sync, `PowerScheduler.wake()`, watcher restart, hotkey listener restart, and folder crawl/orphan purge. **Optimize / Vacuum Database** MUST run asynchronously (`_VacuumWorker`) and **Re-index All Files** MUST invoke `reset_all_for_reindex()` followed by a full crawl.

#### Scenario: Testing an alternate Ollama host without mutating active services
- **WHEN** a user enters a different Ollama URL in Settings and clicks **Test Connection** then **Cancel**
- **THEN** the probe uses an isolated temporary `OllamaClient`, leaving the running daemon's `ollama.base_url`, active model, and index untouched

### Requirement: Clean Graceful Shutdown of All Services and Resources
`SemSearchApp.shutdown()` MUST unregister D-Bus desktop providers, stop `hotkey_listener`, `tray`, `scheduler`, `watcher`, and `index_service`, join any active crawl thread, shut down `panel` and `settings_dialog` (joining search/preview/vacuum workers), close `ollama`, `db`, and `code_db`, close/remove the IPC socket, release `instance.lock`, and quit the event loop.

#### Scenario: Exiting SemSearch from tray or close event
- **WHEN** `SemSearchApp.shutdown()` executes
- **THEN** all worker threads, HTTP pools, SQLite connections, D-Bus registrations, and IPC sockets/locks are cleanly terminated and released

### Requirement: Lazy-Loaded JSON Internationalization (22 Locales + English)
`semsearch.i18n` MUST load non-English translation catalogs lazily from `src/semsearch/i18n/locales/<lang>.json` (22 JSON files plus inline English for 23 supported languages, with RTL for `ar`, `fa`, `he`), caching only requested languages in `_loaded_catalogs`. `detect_system_language()` MUST inspect `LC_ALL`, `LC_MESSAGES`, `LANGUAGE`, and `LANG` in order, walking colon-separated `LANGUAGE` fallback lists (e.g., `ca:es:en`) until the first supported language matches before falling back to `LANG` / `locale.getlocale()` / `'en'`.

#### Scenario: Lazy locale loading and colon-separated LANGUAGE fallback
- **WHEN** `LANGUAGE="ca:es:en"` is set in the environment and `set_language("auto")` runs
- **THEN** `detect_system_language()` resolves to `"es"` and `tr()` lazily loads and caches only `locales/es.json`
