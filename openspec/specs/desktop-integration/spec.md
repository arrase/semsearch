# desktop-integration Specification

## Purpose
Provides composable headless CLI commands, user-space desktop/provider installation, native D-Bus search providers for KDE KRunner and GNOME Shell, Linux desktop deep-link actions, selective inotify filesystem monitoring, and power/schedule policy enforcement.

## Requirements

### Requirement: Composable CLI Flags and Headless Subcommands
`semsearch` (`main.py` + `cli.py`) MUST initialize `set_language()` from `ConfigManager` prior to constructing `argparse` so `--help` is localized. It MUST support mutually exclusive GUI/IPC activation flags (`--daemon`, `--panel`, `--toggle`, `--settings`) and headless subcommands without launching Qt GUI windows:
- `semsearch query "<query>" [--code] [--kind K]... [--limit N] [--json]` (supports repeatable/comma-separated `--kind`, inline filters, formatted stdout lines or JSON array serialized via `hit_to_dict()`, returning exit code `2` on invalid `--kind`).
- `semsearch status [--json]` (reports `data_dir`, `configured_model`, `ollama_url`, `code_enabled`, and `main`/`code` database counts, model/dim metadata, and disk bytes).
- `semsearch reindex [--force]` (forwards `REINDEX` / `REINDEX_FORCE` over IPC if a daemon is running, or performs a standalone crawl/sync and optional `reset_all_for_reindex()` otherwise).
- `semsearch install-desktop [--data-home PATH]` (installs `applications/semsearch.desktop`, `icons/hicolor/scalable/apps/semsearch.svg`, `dbus-1/services/org.semsearch.DesktopSearch.service`, `krunner/dbusplugins/semsearch.desktop`, and `gnome-shell/search-providers/semsearch-search-provider.ini` under `$XDG_DATA_HOME`).

#### Scenario: Executing headless CLI query with JSON output
- **WHEN** `semsearch query "kind:pdf quarterly report" --limit 10 --json` runs from a terminal or script
- **THEN** `SearchService` executes directly against local SQLite/Ollama and writes a valid JSON list of serialized hits to stdout without opening any GUI window

#### Scenario: Installing user-space desktop entries and search providers
- **WHEN** `semsearch install-desktop` runs
- **THEN** the `.desktop` launcher (`Exec="<bin>" --toggle`), SVG icon, D-Bus service file (`org.semsearch.DesktopSearch`), KRunner plugin descriptor, and GNOME Shell search provider `.ini` are written under `$XDG_DATA_HOME`

### Requirement: Native Linux D-Bus Desktop Search Providers (KRunner & GNOME Shell)
`DesktopSearchProviders` (`gui/desktop_providers.py`) MUST register service name `org.semsearch.DesktopSearch` on the session D-Bus with two `QDBusVirtualObject` endpoints returning up to 10 ranked hits (`prefix_last=True`):
- `/krunner` implementing `org.kde.krunner1`: `Actions` (exposing secondary action `"reveal"` / *Show in Files*), `Match` (returning remote match tuples with filename, icon, category, formatted folder + excerpt subtext, and normalized relevance), and `Run` (executing `execute_hit_action` or `reveal_in_file_manager`).
- `/org/semsearch/SearchProvider` implementing `org.gnome.Shell.SearchProvider2`: `GetInitialResultSet`, `GetSubsearchResultSet`, `GetResultMetas`, `ActivateResult` (deep-link open), and `LaunchSearch` (emitting `launch_panel_requested(query)` to open the Quick Panel pre-filled with the query).

#### Scenario: Querying and activating results via KRunner and GNOME Shell D-Bus interfaces
- **WHEN** KDE KRunner calls `org.kde.krunner1.Match`/`Run` on `/krunner` or GNOME Shell calls `GetInitialResultSet`/`GetResultMetas`/`ActivateResult`/`LaunchSearch` on `/org/semsearch/SearchProvider`
- **THEN** SemSearch returns formatted metadata from `SearchService`, opens activated hits at their exact page/line/timestamp, and opens the Quick Panel pre-filled on `LaunchSearch`

### Requirement: Desktop Deep Link Navigation, Clipboard, and Terminal Actions
`services/actions.py` MUST implement native Linux desktop actions:
- `open_code_at(path, line, editor_preference)`: when `editor_preference == "jetbrains"`, probes `JETBRAINS_IDES` (`pycharm`, `idea`, `clion`, `webstorm`, `goland`, `rustrover`, `rider`) with `--line` before other editors; supports `code`, `cursor`, `zed`, `subl`, and `xdg-open` fallback.
- `open_pdf_at(path, page)`: queries the user's default XDG PDF viewer (`xdg-mime query default application/pdf`) and prefers it among `papers -i`, `okular -p`, `evince -i`, `zathura -P`, always passing `--` before the path, falling back to `xdg-open`.
- `open_media_at(path, seconds)`: invokes `mpv --start=sec -- path`, `vlc --start-time=sec -- path`, or `xdg-open`.
- `reveal_in_file_manager(path)`: calls `org.freedesktop.FileManager1.ShowItems([Path.resolve().as_uri()], "")` via `PySide6.QtDBus.QDBusInterface`, falling back to `dolphin`/`nautilus`/`nemo --select` and `xdg-open` on the parent directory.
- `copy_to_clipboard(text)` & `copy_file_to_clipboard(path)`: use native `QGuiApplication.clipboard()` (`QMimeData.setUrls` + `file://` URI text for files) when a Qt GUI instance is active, with `wl-copy` / `xclip -selection clipboard` fallback in headless CLI mode.
- `open_terminal_at(path)`: opens the first available terminal in `STANDARD_TERMINALS` (`xdg-terminal-exec`, `kitty`, `alacritty`, `ghostty`, `konsole`, `gnome-terminal`, `wezterm`, `xfce4-terminal`, `xterm`) at the file's directory.

#### Scenario: Opening code in JetBrains, PDF in default viewer, and revealing via QtDBus
- **WHEN** `execute_hit_action()`, `reveal_in_file_manager()`, or `open_terminal_at()` is invoked
- **THEN** JetBrains preference selects installed JetBrains IDEs, PDFs respect the default XDG viewer (`papers`/`okular`/`evince -i`/`zathura` with `--`), file reveal invokes `FileManager1.ShowItems` via `QDBusInterface`, and terminal launches at the target directory

### Requirement: Selective Non-Recursive Inotify Monitoring and Differential Debouncing
`FileSystemWatcher` MUST monitor eligible directories using non-recursive (`recursive=False`) Linux inotify watches (skipping dot-folders, `SKIPPED_FOLDER_NAMES`, `.noindex`, and `excluded_folders`) with dynamic watch registration on subdirectory creation, `0.75s` debounce for general folders and `60s` quiet window / `600s` ceiling for code repositories, zero-wakeup idle waiting on `_wake_event`, and `on_moved` path updates (`db.move_file()`) that skip re-enqueuing files whose size and mtime remain unchanged.

#### Scenario: Selective directory watching and unchanged move handling
- **WHEN** `FileSystemWatcher` starts on a workspace containing `.git` and `node_modules` and an indexed file is later renamed
- **THEN** excluded/hidden directories receive zero inotify watches, renamed files update their DB paths without re-embedding, and idle debounce threads sleep on `_wake_event`

### Requirement: Device-Filtered Power Policy and Zero-Polling Scheduler
`PowerScheduler` MUST inspect `/sys/class/power_supply/` ignoring entries with `scope == "Device"` (wireless peripherals), enforce battery and overnight-capable operating-hour policies via `index_service.pause(..., by_policy=True)` / `resume(by_policy=True)`, and suspend periodic polling completely (`_wake_event.wait()`) whenever both `index_on_battery=True` and `index_schedule_enabled=False` until `wake()` or `stop()` is invoked.

#### Scenario: Battery discharge pause and zero polling when policies are disabled
- **WHEN** a system battery (`scope != "Device"`) discharges while `index_on_battery=False`, or when both battery and schedule policies are inactive
- **THEN** indexing pauses with policy reason `"AC power"` on system battery discharge, peripheral batteries are ignored, and no periodic timer wakes occur when both policies are disabled
