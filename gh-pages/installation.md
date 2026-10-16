# Installation & CLI Setup

This guide covers installing system prerequisites, setting up **Ollama v0.40.4+** with the **`embeddinggemma-2`** multimodal model, installing **SemSearch**, and using its command-line interface.

---

## 1. System Requirements

| Component | Minimum Requirement | Notes |
| :--- | :--- | :--- |
| **Operating System** | Linux Desktop (X11 or Wayland) | Uses XDG paths, `inotify`, `/sys/class/power_supply`, and system tray. |
| **Python** | Python `>= 3.11` | Required for modern typing and PySide6 `>= 6.8.0`. |
| **Ollama** | **v0.40.4 or higher** | Required for multimodal `/api/embed` support across text, images, and audio. |
| **FFmpeg** | `ffmpeg` *(recommended)* | Required for decoding audio streams and sampling video keyframes. |
| **Clipboard Utils** | `wl-copy` (Wayland) or `xclip` (X11) | Optional, used when copying file URIs with <kbd>Ctrl</kbd>+<kbd>C</kbd>. |

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

### Install or Update Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Pull `embeddinggemma-2`

Pull the default multimodal embedding model:

```bash
ollama pull embeddinggemma-2
```

Or pull a quantized GGUF variant directly from Hugging Face:

```bash
ollama pull hf.co/ggml-org/embeddinggemma-2-GGUF:BF16
```

---

## 3. Installing SemSearch

You can install SemSearch directly from GitHub as an isolated desktop/CLI tool using **`uv`** or **`pipx`**, or inside a local virtual environment.

### Method 1: Using `uv` (Recommended)

```bash
uv tool install git+https://github.com/arrase/semsearch.git
```

To upgrade to the latest release:

```bash
uv tool upgrade semsearch
```

### Method 2: Using `pipx`

```bash
pipx install git+https://github.com/arrase/semsearch.git
```

To upgrade:

```bash
pipx upgrade semsearch
```

### Method 3: From Source (Local Virtual Environment)

```bash
git clone https://github.com/arrase/semsearch.git
cd semsearch
uv venv
uv pip install -e .
```

Run tests using the virtual environment:

```bash
.venv/bin/pytest
```

---

## 4. CLI Usage & Single-Instance Forwarding

Once installed, the `semsearch` entry point launches the application or forwards commands over local Unix domain socket IPC (`semsearch_ipc_<uid>`) if an instance is already running:

```bash
semsearch [--panel | --window | --settings | --daemon]
```

| Command | IPC Action | Description |
| :--- | :--- | :--- |
| `semsearch` | `SHOW_WINDOW` | Starts SemSearch (if not running) and opens the Full Desktop Search Window. |
| `semsearch --window` | `SHOW_WINDOW` | Brings the Full Desktop Search Window to the foreground. |
| `semsearch --panel` | `SHOW_PANEL` | Opens or focuses the Floating Quick Search Panel (ideal for custom WM keybindings). |
| `semsearch --settings` | `SHOW_SETTINGS` | Opens the 4-tab Settings Dialog directly. |
| `semsearch --daemon` | `DAEMON` | Starts SemSearch silently in the background (system tray icon and global hotkey listener only). |

### Binding `semsearch --panel` in Window Managers / Wayland Compositors

While SemSearch includes a built-in global hotkey listener (`pynput`), Wayland compositors (GNOME Wayland, KDE KWin Wayland, Sway, Hyprland) isolate global key events. You can bind `semsearch --panel` directly in your desktop environment or compositor configuration for instant zero-latency activation:

=== "Hyprland (`~/.config/hypr/hyprland.conf`)"

    ```ini
    bind = CTRL SHIFT, Space, exec, semsearch --panel
    ```

=== "Sway / i3 (`~/.config/sway/config`)"

    ```ini
    bindsym Ctrl+Shift+space exec semsearch --panel
    ```

=== "GNOME / KDE Plasma"

    Add a custom global shortcut in **Settings → Keyboard → Custom Shortcuts** that executes `semsearch --panel` (or `~/.local/bin/semsearch --panel`) on <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Space</kbd>.

---

## 5. Desktop Autostart & Storage Paths

Enabling **"Start SemSearch automatically on desktop login"** in **Settings → General** writes a FreeDesktop entry to `~/.config/autostart/semsearch.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=SemSearch
Comment=Linux desktop semantic search engine
Exec=/home/username/.local/bin/semsearch --daemon
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
```

### Summary of Local File Paths

| Path | Purpose |
| :--- | :--- |
| `~/.config/semsearch/config.json` | User configuration file (`AppConfig`). |
| `~/.config/autostart/semsearch.desktop` | FreeDesktop login autostart entry. |
| `~/.local/share/semsearch/index.sqlite` | General documents, PDFs, images, screenshots, audio, and video index. |
| `~/.local/share/semsearch/code.sqlite` | Dedicated source code and Jupyter notebook index. |
