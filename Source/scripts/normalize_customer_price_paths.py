"""Preview or normalize historical full customer price-file paths."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from windsor_widget.config import load_settings
from windsor_widget.db.models import CustomerPriceFile
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.services.customer_link_admin import (
    normalize_existing_price_file_paths,
    price_file_relative_path,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    args = parser.parse_args()

    if args.commit and (not args.username or not args.display_name):
        raise SystemExit("--username and --display-name are required with --commit")

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            changes = []
            skipped = []
            for row in session.scalars(select(CustomerPriceFile)):
                try:
                    relative = price_file_relative_path(row.file_path)
                except ValueError as exc:
                    skipped.append((row.file_name, str(exc)))
                    continue
                if relative != row.file_path:
                    changes.append((row.file_path, relative))

            print(f"Price files requiring normalisation: {len(changes)}")
            print(f"Paths requiring manual review: {len(skipped)}")
            for before, after in changes[:20]:
                print(f"  {before} -> {after}")
            if not args.commit:
                print("Preview only; no records were changed.")
                return 0

            actor = ensure_app_user(
                session,
                username=args.username,
                display_name=args.display_name,
            )
            converted, skipped_count = normalize_existing_price_file_paths(
                session,
                actor_user_id=actor.user_id,
            )
            session.commit()
            print(f"Converted={converted}; skipped={skipped_count}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
