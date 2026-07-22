from __future__ import annotations

import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    CustomerAccount,
    ImportBatch,
    ImportRow,
    Item,
    PurchaseDocument,
    PurchaseLine,
    Supplier,
)
from windsor_widget.imports.transaction_promotion import promote_transaction_batches


def _payload(values: dict[str, str | None]) -> str:
    return json.dumps({"raw_values": list(values.values()), "values": values})


def _add_batch(
    session: Session,
    source_type: str,
    rows: list[dict[str, str | None]],
) -> None:
    batch = ImportBatch(
        source_type=source_type,
        source_file_name=f"{source_type}.TXT",
        file_sha256=(source_type * 4)[:64],
        status="approved",
        row_count=len(rows),
        accepted_row_count=len(rows),
    )
    session.add(batch)
    session.flush()
    for row_number, values in enumerate(rows, start=1):
        session.add(
            ImportRow(
                import_batch_id=batch.import_batch_id,
                row_number=row_number,
                raw_text="source row",
                raw_json=_payload(values),
                natural_key=str(row_number),
                row_sha256=f"{source_type}-{row_number}".ljust(64, "0")[:64],
                status="accepted",
                issue_count=0,
            )
        )
    session.flush()


def _sales_values() -> dict[str, str | None]:
    return {
        "Record ID": "C1",
        "Invoice No.": "INV1",
        "Co./Last Name": "Customer A",
        "Date": "1/07/2026",
        "Item Number": "I1",
        "Quantity": "1",
        "Price": "$1.00",
        "Total": "$1.00",
    }


def _purchase_values(number: str) -> dict[str, str | None]:
    return {
        "Record ID": "S1",
        "Purchase No.": number,
        "Co./Last Name": "Supplier A",
        "Date": "2/07/2026",
        "Item Number": "I1",
        "Quantity": "1",
        "Price": "$1.00",
        "Total": "$1.00",
    }


def test_case_only_purchase_numbers_share_one_document_identity() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                CustomerAccount(
                    myob_record_id="C1",
                    display_name="Customer A",
                    normalized_name="customer a",
                    is_active=True,
                ),
                Supplier(
                    myob_record_id="S1",
                    display_name="Supplier A",
                    normalized_name="supplier a",
                    is_active=True,
                ),
                Item(
                    item_number="I1",
                    item_name="Item A",
                    normalized_name="item a",
                    is_active=True,
                ),
            ]
        )
        session.flush()
        _add_batch(session, "sales_transactions", [_sales_values()])
        _add_batch(session, "cover_order_snapshot", [_sales_values()])
        _add_batch(
            session,
            "purchase_transactions",
            [_purchase_values("STOCK"), _purchase_values("stock")],
        )

        preview = promote_transaction_batches(session, commit=False)
        purchase = next(
            change
            for change in preview.changes
            if change.source_type == "purchase_transactions"
        )

        assert purchase.documents_created == 1
        assert purchase.lines_created == 2
        assert session.scalar(select(func.count(PurchaseDocument.purchase_document_id))) == 0
        assert session.scalar(select(func.count(PurchaseLine.purchase_line_id))) == 0
