"""SemSearch application entry point and CLI dispatcher."""

import argparse
import sys
from semsearch.gui.app import SemSearchApp, forward_ipc_command
from semsearch.i18n import tr


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description=tr("SemSearch - Linux Desktop Semantic Search Engine"))
    parser.add_argument("--panel", action="store_true", help=tr("Open floating quick search panel"))
    parser.add_argument("--window", action="store_true", help=tr("Open full desktop search window"))
    parser.add_argument("--settings", action="store_true", help=tr("Open settings dialog"))
    parser.add_argument("--daemon", action="store_true", help=tr("Run background daemon with system tray icon only"))
    return parser


def main() -> None:
    """Run SemSearch CLI dispatcher or start application lifecycle."""
    args = build_parser().parse_args()

    action = "SHOW_WINDOW"
    if args.daemon:
        action = "DAEMON"
    elif args.panel:
        action = "SHOW_PANEL"
    elif args.window:
        action = "SHOW_WINDOW"
    elif args.settings:
        action = "SHOW_SETTINGS"

    if forward_ipc_command(action):
        sys.exit(0)

    app = SemSearchApp(sys.argv)
    app.start()
    if action != "DAEMON":
        app.handle_cli_action(action)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
