"""SemSearch application entry point and CLI dispatcher."""

import argparse
import sys
from pathlib import Path

from semsearch.cli import run_install_desktop, run_query, run_reindex, run_status
from semsearch.config import ConfigManager
from semsearch.gui.app import SemSearchApp, forward_ipc_command
from semsearch.i18n import set_language, tr


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser with GUI activation flags and headless subcommands."""
    parser = argparse.ArgumentParser(description=tr("SemSearch - Linux Desktop Semantic Search Engine"))
    parser.add_argument("--config", type=Path, default=None, help="Override path to config.json")

    gui_group = parser.add_mutually_exclusive_group()
    gui_group.add_argument("--daemon", action="store_true", help=tr("Run background daemon with system tray icon only"))
    gui_group.add_argument("--panel", action="store_true", help=tr("Open floating quick search panel"))
    gui_group.add_argument("--toggle", action="store_true", help=tr("Toggle the floating quick search panel"))
    gui_group.add_argument("--settings", action="store_true", help=tr("Open settings dialog"))

    subparsers = parser.add_subparsers(dest="command")

    query_parser = subparsers.add_parser("query", help=tr("Search indexed files from the command line"))
    query_parser.add_argument("query", nargs="+", help="Search query text")
    query_parser.add_argument("--code", action="store_true", help="Search source code index")
    query_parser.add_argument("--kind", action="append", default=None, help="Filter by file kind (repeatable)")
    query_parser.add_argument("--limit", type=int, default=20, help="Maximum number of results to return")
    query_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    status_parser = subparsers.add_parser("status", help=tr("Show index status and storage summary"))
    status_parser.add_argument("--json", action="store_true", help="Output status as JSON")

    reindex_parser = subparsers.add_parser("reindex", help=tr("Re-index all configured folders"))
    reindex_parser.add_argument("--force", action="store_true", help="Reset all indexed records before re-crawling")

    install_parser = subparsers.add_parser(
        "install-desktop",
        help=tr("Install desktop entry, icon, and search providers"),
    )
    install_parser.add_argument(
        "--data-home",
        type=Path,
        default=None,
        help="Override XDG_DATA_HOME target directory",
    )

    return parser


def _parse_config_override(argv: list[str] | None) -> Path | None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    known, _ = pre.parse_known_args(argv)
    return known.config if isinstance(known.config, Path) else None


def _dispatch_subcommand(args: argparse.Namespace, config_manager: ConfigManager) -> int:
    if args.command == "query":
        return run_query(
            " ".join(args.query),
            is_code=args.code,
            kinds=args.kind,
            limit=args.limit,
            as_json=args.json,
            config_manager=config_manager,
        )
    if args.command == "status":
        return run_status(as_json=args.json, config_manager=config_manager)
    if args.command == "reindex":
        return run_reindex(force=args.force, forward_ipc=forward_ipc_command, config_manager=config_manager)
    return run_install_desktop(data_home=args.data_home)


def _resolve_gui_action(args: argparse.Namespace) -> str:
    if args.toggle:
        return "TOGGLE"
    if args.panel:
        return "PANEL"
    if args.settings:
        return "SETTINGS"
    return "DAEMON"


def main(argv: list[str] | None = None) -> int:
    """Run SemSearch CLI dispatcher or start application lifecycle."""
    config_path = _parse_config_override(argv)
    config_manager = ConfigManager(config_path=config_path) if config_path is not None else ConfigManager()
    set_language(config_manager.config.language)
    args = build_parser().parse_args(argv)

    if args.command is not None:
        return _dispatch_subcommand(args, config_manager)

    action = _resolve_gui_action(args)
    if forward_ipc_command(action):
        return 0

    qt_argv = [sys.argv[0], *argv] if argv is not None else sys.argv
    app = SemSearchApp(qt_argv, config_manager=config_manager)
    app.start(daemon_mode=args.daemon)
    if app.is_shutdown:
        return 0
    if action == "TOGGLE":
        app.handle_cli_action("PANEL")
    elif action != "DAEMON":
        app.handle_cli_action(action)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
