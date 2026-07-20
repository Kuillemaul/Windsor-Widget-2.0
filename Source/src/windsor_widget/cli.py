"""Small operational commands that are safe to run before the UI exists."""

from __future__ import annotations

import argparse
from pathlib import Path

from windsor_widget.config import load_settings
from windsor_widget.db.bootstrap import (
    ensure_development_database,
    upgrade_development_database,
    verify_development_database,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="windsor-widget")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser(
        "check-config",
        help="validate a development configuration without connecting to SQL Server",
    )
    check.add_argument("config", type=Path)

    setup = subcommands.add_parser(
        "setup-dev-database",
        help="create, migrate and verify only WindsorWidgetV2_DEV",
    )
    setup.add_argument("config", type=Path)
    setup.add_argument(
        "--alembic-config",
        type=Path,
        default=Path("alembic.ini"),
        help="path to alembic.ini (default: ./alembic.ini)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "check-config":
        settings = load_settings(args.config)
        print(f"Configuration is safe: {settings.application_name}")
        print(f"Database target: {settings.database.database}")
        print(f"Operational root: {settings.folders.root}")
        return 0

    if args.command == "setup-dev-database":
        settings = load_settings(args.config)
        result = ensure_development_database(settings)
        print(f"Database {result.database}: {result.status}.")
        upgrade_development_database(
            args.config,
            alembic_config_path=args.alembic_config,
        )
        verification = verify_development_database(settings)
        print(f"Alembic revision: {verification.alembic_revision}")
        print(f"Verified application tables: {len(verification.tables) - 1}")
        print("Windsor Widget v2 development database is ready.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
