"""Preview or apply preferred-supplier inference from MYOB purchase bills."""

from __future__ import annotations

import argparse
from pathlib import Path

from windsor_widget.config import load_settings
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.services.supplier_bill_links import (
    sync_supplier_links_from_bills,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument(
        "--overwrite-manual-preferred",
        action="store_true",
        help="Allow latest bill history to replace a user-selected preferred supplier.",
    )
    args = parser.parse_args()

    if args.commit and (not args.username or not args.display_name):
        raise SystemExit("--username and --display-name are required with --commit")

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            actor = None
            if args.commit:
                actor = ensure_app_user(
                    session,
                    username=args.username,
                    display_name=args.display_name,
                )

            progress_state = {"phase": None}

            def show_progress(phase: str, current: int, total: int) -> None:
                if progress_state["phase"] not in (None, phase):
                    print()

                progress_state["phase"] = phase
                width = 30
                ratio = 1.0 if total <= 0 else min(max(current / total, 0.0), 1.0)
                filled = int(width * ratio)
                bar = "#" * filled + "-" * (width - filled)
                percent = int(ratio * 100)

                print(
                    f"\r{phase:<36} [{bar}] "
                    f"{percent:3d}%  {current:,}/{total:,}",
                    end="",
                    flush=True,
                )

            summary = sync_supplier_links_from_bills(
                session,
                commit=args.commit,
                actor=actor,
                preserve_manual_preferred=not args.overwrite_manual_preferred,
                progress=show_progress,
            )

            if progress_state["phase"] is not None:
                print()

            print(f"Bill lines considered: {summary.bill_lines_considered}")
            print(f"Purchased items: {summary.purchased_items}")
            print(f"Supplier-item pairs: {summary.item_supplier_pairs}")
            print(f"Links to create: {summary.links_created}")
            print(f"Links to update: {summary.links_updated}")
            print(f"Preferred flags to change: {summary.preferred_changed}")
            print(
                "Manual preferred suppliers preserved: "
                f"{summary.manual_preferred_preserved}"
            )
            print(
                "User-rejected links preserved: "
                f"{summary.user_rejections_preserved}"
            )

            if args.commit:
                session.commit()
                print("Committed supplier links and preferred suppliers.")
            else:
                session.rollback()
                print("Preview only; no records were changed.")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
