# Installation, CLI & Wayland Desktop Setup

This guide covers installing system prerequisites, setting up **Ollama v0.40.4+** with **`embeddinggemma-2`**, installing **SemSearch**, using all headless CLI subcommands, and configuring **Wayland global shortcuts**, **KDE KRunner**, and **GNOME Shell Search Provider**.

---

## 1. System Requirements

| Component | Minimum Requirement | Notes |
| :--- | :--- | :--- |
| **Operating System** | Linux Desktop (Wayland or X11) | Uses XDG paths, `inotify`, `/sys/class/power_supply`, QtDBus, and QtMultimedia. |
| **Python** | Python `>= 3.11` | Required for modern typing and PySide6 `>= 6.8.0`. |
| **Ollama** | **v0.40.4 or higher** | Required for multimodal `/api/embed` support across text, images, and audio. |
| **FFmpeg / FFprobe** | `ffmpeg` *(recommended)* | Used for decoding audio streams, probing media duration, extracting RMS waveforms, and sampling video keyframes. |
| **Clipboard Utils** | `wl-copy` / `xclip` *(optional)* | GUI mode uses native Qt clipboard (`QGuiApplication.clipboard()`); CLI fallback uses `wl-copy` (Wayland) or `xclip` (X11). |

### Installing System Dependencies

=== "Ubuntu / Debian"

    ```bash
    sudo apt update
    sudo apt install -y ffmpeg wl-clipboard xclip
    ```

=== "Fedora"

    ```bash
    sudo dnf install -y ffmpeg wl-clipboard xclip
    ```

=== "Arch Linux"

    ```bash
    sudo pacman -S ffmpeg wl-clipboard xclip
    ```

=== "openSUSE"

    ```bash
    sudo zypper install ffmpeg wl-clipboard xclip
    ```

---

## 2. Ollama Setup & Multimodal Model

SemSearch requires **Ollama v0.40.4+** running locally (`http://localhost:11434`) or on your network.

```bash
# Install or update Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the default multimodal embedding model
ollama pull embeddinggemma-2

# Or pull a quantized GGUF variant directly from Hugging Face
ollama pull hf.co/ggml-org/embeddinggemma-2-GGUF:BF16
```

---

## 3. Installing SemSearch

Install SemSearch directly from GitHub as an isolated tool using **`uv`** or **`pipx`**, or from source in a virtual environment.

=== "`uv` (Recommended)"

    ```bash
    uv tool install git+https://github.com/arrase/semsearch.git
    # Upgrade later:
    uv tool upgrade semsearch
    ```

=== "`pipx`"

    ```bash
    pipx install git+https://github.com/arrase/semsearch.git
    # Upgrade later:
    pipx upgrade semsearch
    ```

=== "From Source"

    ```bash
    git clone https://github.com/arrase/semsearch.git
    cd semsearch
    uv venv
    uv pip install -e .

    # Run the test suite:
    QT_QPA_PLATFORM=offscreen .venv/bin/pytest
    ```

---

## 4. CLI Usage, Subcommands & IPC Forwarding

SemSearch initializes your configured interface language before parsing arguments (so `semsearch --help` is localized). GUI activation flags forward instantly to a running instance over the owner-private `AF_UNIX` socket (`$XDG_RUNTIME_DIR/semsearch/ipc.sock`), while headless subcommands execute directly without opening a window.

### GUI & Daemon Activation Flags

| Command | IPC Action | Description |
| :--- | :--- | :--- |
| `semsearch` or `semsearch --daemon` | `DAEMON` | Start anchored in the system tray (runs the 5-step Onboarding Wizard on first launch). |
| `semsearch --toggle` | `TOGGLE` | Toggle the Floating Quick Search Panel open/closed (universal shortcut command). |
| `semsearch --panel` | `PANEL` | Open and focus the Floating Quick Search Panel. |
| `semsearch --settings` | `SETTINGS` | Open the 4-tab Settings Dialog directly. |

### Headless Subcommands

| Command | Description |
| :--- | :--- |
| `semsearch query "<text>" [--code] [--kind K] [--limit 20] [--json]` | Run a hybrid search from the terminal or scripts. Supports repeatable/comma-separated `--kind` (`pdf`, `doc`, `image`, `screenshot`, `audio`, `video`, `code`), inline filters (`code:`, `kind:`, `ext:`, `path:`, `after:`), and structured `--json` output. |
| `semsearch status [--json]` | Print `data_dir`, configured model, Ollama URL, file/pending/failed/segment/vector counts, stored `embedding_model`/`embedding_dim` metadata, and disk usage for `index.sqlite` and `code.sqlite`. |
| `semsearch reindex [--force]` | Trigger a crawl/sync (or full `reset_all_for_reindex()` + re-crawl with `--force`). Forwards `REINDEX` / `REINDEX_FORCE` to a running daemon via IPC, or runs standalone if no daemon is active. |
| `semsearch install-desktop [--data-home PATH]` | Install user-space `.desktop` launcher, SVG icon, D-Bus service, KDE KRunner plugin, and GNOME Shell Search Provider descriptors. |

```bash
# Search PDFs modified in the last 30 days and pipe JSON to jq:
semsearch query "quarterly budget kind:pdf after:30d" --json | jq '.[0:3]'

# Search source code index directly:
semsearch query "OllamaClient" --code --limit 10

# Inspect index status as JSON:
semsearch status --json

# Force a clean re-index of all configured folders:
semsearch reindex --force
```

---

## 5. Wayland Global Shortcuts & Compositor Keybindings

### Automatic Global Shortcut (Wayland Portal & X11)
* **Wayland (`$WAYLAND_DISPLAY` / `XDG_SESSION_TYPE=wayland`)**: SemSearch automatically registers `Ctrl+Shift+Space` via the standard **XDG Desktop Portal `org.freedesktop.portal.GlobalShortcuts`** interface (supported natively on **KDE Plasma 6**, **GNOME 48+**, and **Hyprland**).
* **X11 (`$DISPLAY`)**: Falls back automatically to `pynput` `GlobalHotKeys` (`super`/`meta`/`win` mapped to `<cmd>`).
* Check **Settings → General** at any time to verify which backend is active (`Wayland XDG GlobalShortcuts portal` or `X11 pynput`).

### Universal Compositor Keybinding (`semsearch --toggle`)
On Wayland compositors without the `GlobalShortcuts` portal (such as Sway, older GNOME releases, or custom wlroots setups), bind your shortcut directly to `semsearch --toggle` for instant zero-latency toggle behavior:

=== "Hyprland (`~/.config/hypr/hyprland.conf`)"

    ```ini
    bind = CTRL SHIFT, Space, exec, semsearch --toggle
    ```

=== "Sway / i3 (`~/.config/sway/config`)"

    ```ini
    bindsym Ctrl+Shift+space exec semsearch --toggle
    ```

=== "GNOME / KDE Plasma"

    Add a custom global shortcut in **Settings → Keyboard → Custom Shortcuts** executing `semsearch --toggle` (or `~/.local/bin/semsearch --toggle`) on <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Space</kbd>.

---

## 6. KDE KRunner & GNOME Shell Search Provider Setup

Run `semsearch install-desktop` once after installing SemSearch:

```bash
semsearch install-desktop
```

This writes five user-space files under `$XDG_DATA_HOME` (default `~/.local/share/`):

| Installed File | Purpose |
| :--- | :--- |
| `applications/semsearch.desktop` | Application menu launcher (`Exec="<bin>" --toggle` + Settings desktop action) |
| `icons/hicolor/scalable/apps/semsearch.svg` | Scalable vector application icon |
| `dbus-1/services/org.semsearch.DesktopSearch.service` | Session D-Bus activation service (`Exec="<bin>" --daemon`) |
| `krunner/dbusplugins/semsearch.desktop` | KDE Plasma KRunner D-Bus runner plugin (`/krunner`, `org.kde.krunner1`) |
| `gnome-shell/search-providers/semsearch-search-provider.ini` | GNOME Shell Overview search provider (`/org/semsearch/SearchProvider`, `org.gnome.Shell.SearchProvider2`) |

!!! tip "GNOME Shell System Search Providers Directory"
    On Linux distributions where GNOME Shell only scans `/usr/share/gnome-shell/search-providers/` rather than `~/.local/share/gnome-shell/search-providers/`, link the installed `.ini` file with a single command and restart your GNOME session:

    ```bash
    sudo ln -sf ~/.local/share/gnome-shell/search-providers/semsearch-search-provider.ini \
      /usr/share/gnome-shell/search-providers/semsearch-search-provider.ini
    ```

---

## 7. Desktop Autostart & Local Storage Paths

Enabling **"Start SemSearch automatically on desktop login"** in **Settings → General** manages `~/.config/autostart/semsearch.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=SemSearch
Comment=Linux desktop semantic search engine
Exec="/home/username/.local/bin/semsearch" --daemon
Icon=semsearch
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
```

### Summary of Local File Paths

| Path | Purpose |
| :--- | :--- |
| `~/.config/semsearch/config.json` | User configuration file (`AppConfig`, atomic `0600` persistence). |
| `~/.config/autostart/semsearch.desktop` | FreeDesktop login autostart entry. |
| `$XDG_DATA_HOME/semsearch/index.sqlite` | General documents, PDFs, images, SVGs, screenshots, audio, and video index (default `~/.local/share/semsearch/`). |
| `$XDG_DATA_HOME/semsearch/code.sqlite` | Dedicated source code and Jupyter notebook index. |
| `~/.cache/semsearch/thumbs/` | Disk cache for async video preview thumbnails (max 200 PNGs, LRU pruned). |
| `$XDG_RUNTIME_DIR/semsearch/ipc.sock` & `instance.lock` | Owner-private (`0700`/`0600`) single-instance `flock` lockfile and `AF_UNIX` IPC socket. |
