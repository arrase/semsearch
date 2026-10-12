"""SemSearch application entry point and CLI dispatcher."""

import argparse
import sys
from semsearch.gui.app import SemSearchApp, forward_ipc_command


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="SemSearch - Linux Desktop Semantic Search Engine")
    parser.add_argument("--panel", action="store_true", help="Open floating quick search panel")
    parser.add_argument("--window", action="store_true", help="Open full desktop search window")
    parser.add_argument("--settings", action="store_true", help="Open settings dialog")
    parser.add_argument("--daemon", action="store_true", help="Run background daemon with system tray icon only")
    return parser


def main() -> None:
    """Run SemSearch CLI dispatcher or start application lifecycle."""
    parser = build_parser()
    args = parser.parse_args()

    action = "DAEMON"
    if args.panel:
        action = "SHOW_PANEL"
    elif args.window:
        action = "SHOW_WINDOW"
    elif args.settings:
        action = "SHOW_SETTINGS"

    # Forward to existing instance if running
    if forward_ipc_command(action):
        sys.exit(0)

    # Boot new instance
    app = SemSearchApp(sys.argv)
    app.start()

    if args.panel:
        app.show_panel()
    elif args.window:
        app.show_window()
    elif args.settings:
        app.show_settings()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
