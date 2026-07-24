"""Preview or commit validated Yuchang roll/spool packing data."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, true

from windsor_widget.config import load_settings
from windsor_widget.db.models import Supplier
from windsor_widget.db.models.supplier_documents import SupplierOrderTemplate
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.services.supplier_order_templates import YUCHANG_TEMPLATE_KIND
from windsor_widget.services.yuchang_packing_sync import (
    apply_yuchang_packing_actions,
    build_yuchang_packing_actions,
)


def resolve_template(
    session,
    explicit_path: Path | None,
) -> tuple[Path, Supplier, str]:
    template = session.scalar(
        select(SupplierOrderTemplate).where(
            SupplierOrderTemplate.template_kind == YUCHANG_TEMPLATE_KIND,
            SupplierOrderTemplate.is_active == true(),
        )
    )
    if template is None:
        raise LookupError(
            "No active Yuchang template exists. Save and verify it from a "
            "Yuchang manufacture order first."
        )
    supplier = session.get(Supplier, template.supplier_id)
    if supplier is None:
        raise LookupError("The configured Yuchang supplier no longer exists.")
    if explicit_path is not None:
        path = explicit_path.expanduser().resolve(strict=True)
    else:
        path = (Path(template.folder_path) / template.file_name).resolve(strict=True)
    return path, supplier, str(template.worksheet_name or "Sheet1")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or commit approved Yuchang Roll/Rolls/Spool/Large Spool "
            "packing data. Preview is the default."
        )
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--worksheet")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    args = parser.parse_args()

    if args.commit and (not args.username or not args.display_name):
        raise SystemExit("--username and --display-name are required with --commit")

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or (
        Path(settings.folders.exports)
        / f"yuchang_packing_sync_preview_{timestamp}.csv"
    )

    try:
        with factory() as session:
            workbook_path, supplier, configured_worksheet = resolve_template(
                session,
                args.template,
            )
            worksheet_name = args.worksheet or configured_worksheet
            actions, summary = build_yuchang_packing_actions(
                session,
                supplier=supplier,
                workbook_path=workbook_path,
                worksheet_name=worksheet_name,
            )

            output = output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            fieldnames = list(actions[0].as_csv_dict()) if actions else []
            with output.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                if fieldnames:
                    writer.writeheader()
                    for action in actions:
                        writer.writerow(action.as_csv_dict())

            action_counts = Counter(action.action for action in actions)
            print()
            print("Yuchang packing-data sync")
            print(f"Mode:                       {'COMMIT' if args.commit else 'PREVIEW'}")
            print(f"Template:                   {workbook_path}")
            print(f"Worksheet:                  {worksheet_name}")
            print(f"Supplier:                   {supplier.display_name}")
            print(f"Workbook detail rows:       {summary.workbook_rows:,}")
            print(f"Create supplier links:      {summary.creates:,}")
            print(f"Update packing values:      {summary.updates:,}")
            print(f"Already unchanged:          {summary.unchanged:,}")
            print(f"Held for review/exclusion:  {summary.held:,}")
            for name in sorted(action_counts):
                if name.startswith("held_"):
                    print(f"  {name:<25} {action_counts[name]:,}")
            print(f"Action CSV:                 {output}")

            if args.commit:
                actor = ensure_app_user(
                    session,
                    username=args.username,
                    display_name=args.display_name,
                )
                created, updated = apply_yuchang_packing_actions(
                    session,
                    supplier=supplier,
                    actions=actions,
                    actor_user_id=actor.user_id,
                )
                session.commit()
                print()
                print(f"Committed links created:    {created:,}")
                print(f"Committed links updated:    {updated:,}")
                print("Preferred supplier flags were not changed.")
            else:
                session.rollback()
                print()
                print("Preview only. No database records were changed.")
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
