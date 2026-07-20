"""Small operational commands that are safe to run before the UI exists."""

from __future__ import annotations

import argparse
from pathlib import Path

from windsor_widget.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windsor-widget")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser(
        "check-config",
        help="validate a development configuration without connecting to SQL Server",
    )
    check.add_argument("config", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "check-config":
        settings = load_settings(args.config)
        print(f"Configuration is safe: {settings.application_name}")
        print(f"Database target: {settings.database.database}")
        print(f"Operational root: {settings.folders.root}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
