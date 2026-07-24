"""Preview or import active MYOB purchase orders as manufacture orders.

Preview is the default. Only active item lines with purchase status O and positive
ordered quantity are considered. Existing manufacture orders are never overwritten.
"""

from __future__ import annotations

import argparse
import csv
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, true

from windsor_widget.config import load_settings
from windsor_widget.db.models import (
    AuditEvent,
    ManufactureOrder,
    ManufactureOrderLine,
    PurchaseDocument,
    PurchaseLine,
    Supplier,
)
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.imports.promotion import ensure_app_user
from windsor_widget.services.manufacture_orders import expected_ready_date

ZERO = Decimal("0")


def quantity_for(line: PurchaseLine) -> Decimal:
    order_quantity = Decimal(line.order_quantity or ZERO)
    return order_quantity if order_quantity > ZERO else Decimal(line.quantity or ZERO)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.commit and (not args.username or not args.display_name):
        raise SystemExit("--username and --display-name are required with --commit")

    output = args.output or (
        Path("..")
        / "DEV"
        / "exports"
        / f"manufacture_order_sync_{datetime.now():%Y%m%d_%H%M%S}.csv"
    ).resolve()

    settings = load_settings(args.config)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            rows = session.execute(
                select(PurchaseDocument, PurchaseLine, Supplier)
                .join(
                    PurchaseLine,
                    PurchaseLine.purchase_document_id
                    == PurchaseDocument.purchase_document_id,
                )
                .join(Supplier, Supplier.supplier_id == PurchaseDocument.supplier_id)
                .where(
                    PurchaseLine.is_active == true(),
                    PurchaseLine.item_id.is_not(None),
                    func.upper(func.coalesce(PurchaseLine.purchase_status, "")) == "O",
                    func.upper(func.trim(Supplier.display_name)) != "STOCK",
                )
                .order_by(
                    Supplier.display_name,
                    PurchaseDocument.purchase_no,
                    PurchaseLine.line_sequence,
                )
            ).all()

            grouped: dict[
                uuid.UUID,
                list[tuple[PurchaseDocument, PurchaseLine, Supplier]],
            ] = defaultdict(list)
            for document, line, supplier in rows:
                if quantity_for(line) > ZERO:
                    grouped[document.purchase_document_id].append((document, line, supplier))

            existing_sources = set(
                session.scalars(
                    select(ManufactureOrder.source_purchase_document_id).where(
                        ManufactureOrder.source_purchase_document_id.is_not(None)
                    )
                )
            )
            existing_keys = set(
                session.execute(
                    select(ManufactureOrder.supplier_id, func.lower(ManufactureOrder.order_number))
                ).all()
            )

            candidates = []
            source_existing = 0
            number_collisions = 0
            for document_id, document_rows in grouped.items():
                document, _, supplier = document_rows[0]
                key = (document.supplier_id, document.purchase_no.casefold())
                if document_id in existing_sources:
                    source_existing += 1
                    continue
                if key in existing_keys:
                    number_collisions += 1
                    continue
                total = sum((quantity_for(line) for _, line, _ in document_rows), ZERO)
                candidates.append((document, supplier, document_rows, total))

            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "supplier",
                        "purchase_no",
                        "order_date",
                        "line_count",
                        "ordered_quantity",
                        "proposed_status",
                    ]
                )
                for document, supplier, document_rows, total in candidates:
                    writer.writerow(
                        [
                            supplier.display_name,
                            document.purchase_no,
                            document.last_transaction_date,
                            len(document_rows),
                            total,
                            "in_production",
                        ]
                    )

            print("MYOB manufacture-order sync")
            print("---------------------------")
            print(f"Active status-O item lines:        {len(rows):,}")
            print(f"Positive purchase documents:       {len(grouped):,}")
            print(f"Already linked by source document: {source_existing:,}")
            print(f"Order-number collisions skipped:   {number_collisions:,}")
            print(f"Manufacture orders to create:      {len(candidates):,}")
            print(f"CSV report:                        {output}")

            for document, supplier, document_rows, total in candidates[:20]:
                print(
                    f"  {supplier.display_name:<30} {document.purchase_no:<18} "
                    f"{len(document_rows):>3} lines  {total:>12,.2f}"
                )
            if len(candidates) > 20:
                print(f"  ...and {len(candidates) - 20:,} more in the CSV.")

            if not args.commit:
                session.rollback()
                print("Preview only; no manufacture orders were changed.")
                return 0

            actor = ensure_app_user(
                session,
                username=args.username,
                display_name=args.display_name,
            )
            created_lines = 0
            for document, supplier, document_rows, _ in candidates:
                order_ready = expected_ready_date(
                    session,
                    supplier_id=document.supplier_id,
                    order_date=document.last_transaction_date,
                )
                order = ManufactureOrder(
                    supplier_id=document.supplier_id,
                    source_purchase_document_id=document.purchase_document_id,
                    order_number=document.purchase_no,
                    order_date=document.last_transaction_date,
                    status="in_production",
                    expected_ready_date=order_ready,
                    notes="Imported from active MYOB purchase order status O.",
                    version=1,
                    created_by_user_id=actor.user_id,
                    updated_by_user_id=actor.user_id,
                )
                session.add(order)
                session.flush()
                for _, source_line, _ in document_rows:
                    quantity = quantity_for(source_line)
                    line_ready = expected_ready_date(
                        session,
                        supplier_id=document.supplier_id,
                        item_id=source_line.item_id,
                        order_date=document.last_transaction_date,
                    ) or order_ready
                    session.add(
                        ManufactureOrderLine(
                            manufacture_order_id=order.manufacture_order_id,
                            item_id=source_line.item_id,
                            source_purchase_line_id=source_line.purchase_line_id,
                            line_sequence=source_line.line_sequence,
                            ordered_quantity=quantity,
                            cancelled_quantity=ZERO,
                            expected_ready_date=line_ready,
                            readiness_override="auto",
                            unit_cost=source_line.unit_price,
                            currency_code=source_line.currency_code,
                        )
                    )
                    created_lines += 1
                session.add(
                    AuditEvent(
                        actor_user_id=actor.user_id,
                        action="manufacture_order.imported",
                        entity_type="manufacture_order",
                        entity_id=str(order.manufacture_order_id),
                        source="script",
                        summary=(
                            f"Imported MYOB purchase order {document.purchase_no} "
                            f"for {supplier.display_name} as a manufacture order."
                        ),
                    )
                )

            session.commit()
            print(
                f"Committed {len(candidates):,} manufacture orders and "
                f"{created_lines:,} item lines."
            )
            print(
                "Imported lines are intentionally unallocated. Review customer cover, "
                "MTO and general-stock purpose in the web screen."
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
