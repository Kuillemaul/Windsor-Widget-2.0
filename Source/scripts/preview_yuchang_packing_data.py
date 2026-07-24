"""Create a read-only CSV preview of Yuchang packing data mapped to Widget items."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, true

from windsor_widget.config import load_settings
from windsor_widget.db.models import Item, ItemSupplier, Supplier
from windsor_widget.db.models.supplier_documents import SupplierOrderTemplate
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.services.supplier_order_templates import YUCHANG_TEMPLATE_KIND
from windsor_widget.services.yuchang_packing_preview import (
    build_yuchang_packing_preview_row,
    clean_item_key,
    extract_yuchang_packing_rows,
    workbook_mapping_counts,
)


def resolve_template(session, explicit_path: Path | None) -> tuple[Path, Supplier]:
    if explicit_path is not None:
        path = explicit_path.expanduser().resolve(strict=True)
        template = session.scalar(
            select(SupplierOrderTemplate).where(
                SupplierOrderTemplate.template_kind == YUCHANG_TEMPLATE_KIND,
                SupplierOrderTemplate.is_active == true(),
            )
        )
        if template is None:
            raise LookupError(
                "No active Yuchang supplier template exists in Widget. "
                "Save and verify it from a Yuchang manufacture order first."
            )
        supplier = session.get(Supplier, template.supplier_id)
        if supplier is None:
            raise LookupError("The configured Yuchang supplier no longer exists.")
        return path, supplier

    template = session.scalar(
        select(SupplierOrderTemplate).where(
            SupplierOrderTemplate.template_kind == YUCHANG_TEMPLATE_KIND,
            SupplierOrderTemplate.is_active == true(),
        )
    )
    if template is None:
        raise LookupError(
            "No active Yuchang supplier template exists. "
            "Open a Yuchang manufacture order and use Save and verify template first."
        )
    supplier = session.get(Supplier, template.supplier_id)
    if supplier is None:
        raise LookupError("The configured Yuchang supplier no longer exists.")
    path = (Path(template.folder_path) / template.file_name).resolve(strict=True)
    return path, supplier


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview Yuchang supplier packing data. This command never writes to the database "
            "or changes the source workbook."
        )
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--worksheet", default="Sheet1")
    parser.add_argument("--include-unmapped", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or (
        Path(settings.folders.exports) / f"yuchang_packing_preview_{timestamp}.csv"
    )

    try:
        with factory() as session:
            workbook_path, supplier = resolve_template(session, args.template)
            source_rows = extract_yuchang_packing_rows(
                workbook_path,
                worksheet_name=args.worksheet,
            )
            mapping_counts = workbook_mapping_counts(source_rows)

            items_by_key: dict[str, list[dict[str, object]]] = defaultdict(list)
            item_id_by_key: dict[str, list[object]] = defaultdict(list)
            for item in session.scalars(select(Item).order_by(Item.item_number)):
                key = clean_item_key(item.item_number)
                if not key:
                    continue
                items_by_key[key].append(
                    {
                        "item_id": item.item_id,
                        "item_number": item.item_number,
                        "item_name": item.item_name,
                    }
                )
                item_id_by_key[key].append(item.item_id)

            links_by_item_id = {
                link.item_id: {
                    "match_status": link.match_status,
                    "supplier_item_number": link.supplier_item_number,
                }
                for link in session.scalars(
                    select(ItemSupplier).where(ItemSupplier.supplier_id == supplier.supplier_id)
                )
            }

            preview_rows = []
            for source in source_rows:
                key = clean_item_key(source.item_number)
                if not key and not args.include_unmapped:
                    continue
                matches = items_by_key.get(key, []) if key else []
                link = None
                if len(matches) == 1:
                    link = links_by_item_id.get(matches[0]["item_id"])
                preview_rows.append(
                    build_yuchang_packing_preview_row(
                        source,
                        mapping_count=mapping_counts.get(key, 0) if key else 0,
                        widget_matches=matches,
                        supplier_link=link,
                    )
                )

            output = output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            fieldnames = list(preview_rows[0].as_csv_dict()) if preview_rows else []
            with output.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                if fieldnames:
                    writer.writeheader()
                    for row in preview_rows:
                        writer.writerow(row.as_csv_dict())

            status_counts = Counter(row.preview_status for row in preview_rows)
            mapped_rows = sum(1 for row in source_rows if clean_item_key(row.item_number))
            unique_mappings = len(mapping_counts)
            duplicate_keys = sum(1 for count in mapping_counts.values() if count > 1)
            missing_widget = sum(
                1 for row in preview_rows if row.widget_match_status == "missing"
            )
            complete_pack = sum(
                1
                for row in preview_rows
                if row.parsed_quantity_per_supplier_unit
                and row.parsed_quantity_per_carton
            )

            print()
            print("Yuchang packing-data preview")
            print(f"Template:                  {workbook_path}")
            print(f"Supplier:                  {supplier.display_name}")
            print(f"Detail rows scanned:       {len(source_rows):,}")
            print(f"Mapped workbook rows:      {mapped_rows:,}")
            print(f"Unique mapped item keys:   {unique_mappings:,}")
            print(f"Duplicate item keys:       {duplicate_keys:,}")
            print(f"Missing from Widget:       {missing_widget:,}")
            print(f"Rows with unit + carton:   {complete_pack:,}")
            print(f"Ready:                     {status_counts.get('ready', 0):,}")
            print(f"Partial:                   {status_counts.get('partial', 0):,}")
            print(f"Review:                    {status_counts.get('review', 0):,}")
            print(f"CSV preview:               {output}")
            print()
            print("Preview only. No database records or workbook cells were changed.")
            session.rollback()
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
