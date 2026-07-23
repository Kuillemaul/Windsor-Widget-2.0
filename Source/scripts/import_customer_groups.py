# Preview or apply customer groups and price-file links.

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from windsor_widget.config import load_settings
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.services.customer_group_matching import (
    apply_group_plan,
    build_group_plan,
    write_group_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("source_workbook", type=Path)
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.commit and (not args.username or not args.display_name):
        raise SystemExit("--username and --display-name are required with --commit")

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            plan = build_group_plan(
                session,
                args.source_workbook,
                include_inactive=args.include_inactive,
            )
            report = args.report or (
                Path(settings.folders.exports)
                / f"customer_group_matching_{datetime.now():%Y%m%d_%H%M%S}.csv"
            )
            written = write_group_report(plan, report)

            print("Customer group and price-file matching")
            print(f"Proposed groups: {len(plan.proposals)}")
            print(f"Matched active accounts: {plan.matched_accounts}")
            print(f"Unmatched customer names: {len(plan.unmatched_customer_names)}")
            print(f"Ambiguous customer names: {len(plan.ambiguous_customer_names)}")
            print(f"Groups without safe price file: {len(plan.groups_without_price_file)}")
            print(f"Report: {written}")

            sealy = next((p for p in plan.proposals if p.group_key == "sealy of australia"), None)
            if sealy:
                print(
                    f"Sealy check: accounts={len(sealy.account_ids)}; "
                    f"price_file={sealy.price_file_name or '-'}; "
                    f"confidence={sealy.price_confidence or '-'}"
                )

            if not args.commit:
                print("Preview only; no database records were changed.")
                return 0

            actor = ensure_app_user(
                session,
                username=args.username,
                display_name=args.display_name,
            )
            summary = apply_group_plan(session, plan, actor_user_id=actor.user_id)
            session.commit()
            print(
                f"Applied: groups_created={summary.groups_created}; "
                f"groups_reused={summary.groups_reused}; "
                f"accounts_assigned={summary.accounts_assigned}; "
                f"accounts_already_correct={summary.accounts_already_correct}; "
                f"accounts_skipped_existing_approved={summary.accounts_skipped_existing_approved}; "
                f"price_files_created={summary.price_files_created}; "
                f"price_files_reused={summary.price_files_reused}"
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
