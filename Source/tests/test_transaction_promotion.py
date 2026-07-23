from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    CoverOrderLine,
    CoverOrderSnapshot,
    CustomerAccount,
    ImportBatch,
    ImportIssue,
    ImportRow,
    Item,
    PurchaseLine,
    SalesLine,
    Supplier,
    TransactionLineObservation,
)
from windsor_widget.imports.transaction_promotion import (
    TransactionImportError,
    approve_transaction_batches,
    ensure_app_user,
    promote_transaction_batches,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _payload(values: dict[str, str | None]) -> str:
    return json.dumps({"raw_values": list(values.values()), "values": values})


def _masters(session: Session) -> None:
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


def _sales_values(invoice: str = "INV1") -> dict[str, str | None]:
    return {
        "Record ID": "C1",
        "Invoice No.": invoice,
        "Co./Last Name": "Customer A",
        "Date": "1/07/2026",
        "Item Number": "I1",
        "Quantity": "2",
        "Price": "$3.00",
        "Discount": "0%",
        "Total": "$6.00",
        "Delivery Status": "A",
        "Sale Status": "I",
        "Journal Memo": "Sale; Customer A",
        "Tax Amount": "$.60",
        "Freight Amount": "$.00",
        "Freight Tax Amount": "$.00",
        "Amount Paid": "$6.60",
    }


def _cover_values() -> dict[str, str | None]:
    values = _sales_values("ORD1")
    values["Sale Status"] = "O"
    values["Journal Memo"] = "Sale; Customer A - COVER ORDER"
    return values


def _purchase_values() -> dict[str, str | None]:
    return {
        "Record ID": "S1",
        "Purchase No.": "PO1",
        "Co./Last Name": "Supplier A",
        "Date": "2/07/2026",
        "Item Number": "I1",
        "Quantity": "10",
        "Price": "$2.00",
        "Discount": "0%",
        "Total": "$20.00",
        "Delivery Status": "P",
        "Purchase Status": "O",
        "Order": "10",
        "Received": "0",
        "Tax Amount": "$2.00",
        "Freight Amount": "$.00",
        "Freight Tax Amount": "$.00",
        "Amount Paid": "$.00",
    }


def _add_batch(
    session: Session,
    source_type: str,
    rows: list[dict[str, str | None]],
    *,
    status: str = "staged",
) -> ImportBatch:
    batch = ImportBatch(
        source_type=source_type,
        source_file_name=f"{source_type}.TXT",
        file_sha256=(source_type * 4)[:64],
        status=status,
        row_count=len(rows),
        accepted_row_count=len(rows) if status == "approved" else 0,
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
                status="accepted" if status == "approved" else "parsed",
                issue_count=0,
            )
        )
    session.flush()
    return batch


def _complete_batches(session: Session, *, status: str = "staged") -> None:
    _add_batch(session, "sales_transactions", [_sales_values()], status=status)
    _add_batch(session, "cover_order_snapshot", [_cover_values()], status=status)
    _add_batch(session, "purchase_transactions", [_purchase_values()], status=status)


def test_approval_requires_three_clean_transaction_batches() -> None:
    with Session(_engine()) as session:
        _masters(session)
        _complete_batches(session)
        actor = ensure_app_user(session, username="brad", display_name="Brad")
        summary = approve_transaction_batches(session, actor=actor)

        assert len(summary.approved_batch_ids) == 3
        assert summary.accepted_row_count == 3
        assert {batch.status for batch in session.scalars(select(ImportBatch))} == {
            "approved"
        }
        assert {row.status for row in session.scalars(select(ImportRow))} == {
            "accepted"
        }
        assert session.scalar(select(func.count(AuditEvent.audit_event_id))) == 3


def test_approval_stops_when_any_transaction_issue_exists() -> None:
    with Session(_engine()) as session:
        _masters(session)
        _complete_batches(session)
        batch = session.scalar(
            select(ImportBatch).where(ImportBatch.source_type == "sales_transactions")
        )
        assert batch is not None
        session.add(
            ImportIssue(
                import_batch_id=batch.import_batch_id,
                severity="error",
                issue_code="test",
                message="bad row",
                resolution_status="open",
            )
        )
        actor = ensure_app_user(session, username="brad", display_name="Brad")
        with pytest.raises(TransactionImportError, match="review issue"):
            approve_transaction_batches(session, actor=actor)


def test_preview_then_commit_creates_documents_lines_snapshot_and_lineage() -> None:
    with Session(_engine()) as session:
        _masters(session)
        _complete_batches(session, status="approved")

        preview = promote_transaction_batches(session, commit=False)
        assert preview.document_total == 3
        assert preview.line_total == 3
        assert preview.lines_created == 3
        assert session.scalar(select(func.count(SalesLine.sales_line_id))) == 0

        actor = ensure_app_user(session, username="brad", display_name="Brad")
        committed = promote_transaction_batches(session, commit=True, actor=actor)

        assert committed.mode == "committed"
        assert session.scalar(select(func.count(SalesLine.sales_line_id))) == 1
        assert session.scalar(select(func.count(CoverOrderLine.cover_order_line_id))) == 1
        assert session.scalar(select(func.count(PurchaseLine.purchase_line_id))) == 1
        assert (
            session.scalar(
                select(
                    func.count(
                        TransactionLineObservation.transaction_line_observation_id
                    )
                )
            )
            == 3
        )
        snapshot = session.scalar(select(CoverOrderSnapshot))
        assert snapshot is not None and snapshot.is_current is True
        assert {batch.status for batch in session.scalars(select(ImportBatch))} == {
            "committed"
        }
        assert {row.status for row in session.scalars(select(ImportRow))} == {
            "committed"
        }


def test_identical_duplicate_source_lines_are_preserved_by_sequence() -> None:
    with Session(_engine()) as session:
        _masters(session)
        _add_batch(
            session,
            "sales_transactions",
            [_sales_values(), _sales_values()],
            status="approved",
        )
        _add_batch(
            session, "cover_order_snapshot", [_cover_values()], status="approved"
        )
        _add_batch(
            session, "purchase_transactions", [_purchase_values()], status="approved"
        )
        actor = ensure_app_user(session, username="brad", display_name="Brad")
        summary = promote_transaction_batches(session, commit=True, actor=actor)

        assert summary.changes[0].lines_created == 2
        sequences = list(
            session.scalars(select(SalesLine.line_sequence).order_by(SalesLine.line_sequence))
        )
        assert sequences == [1, 2]


def test_missing_exact_master_reference_stops_preview() -> None:
    with Session(_engine()) as session:
        _masters(session)
        sales = _sales_values()
        sales["Record ID"] = "MISSING"
        _add_batch(session, "sales_transactions", [sales], status="approved")
        _add_batch(
            session, "cover_order_snapshot", [_cover_values()], status="approved"
        )
        _add_batch(
            session, "purchase_transactions", [_purchase_values()], status="approved"
        )

        with pytest.raises(TransactionImportError, match="no promoted master record"):
            promote_transaction_batches(session, commit=False)


def test_cover_order_flag_is_retained() -> None:
    with Session(_engine()) as session:
        _masters(session)
        _complete_batches(session, status="approved")
        actor = ensure_app_user(session, username="brad", display_name="Brad")
        promote_transaction_batches(session, commit=True, actor=actor)
        line = session.scalar(select(CoverOrderLine))
        assert line is not None
        assert line.is_cover_order is True


def test_purchase_only_batch_can_be_approved_and_promoted() -> None:
    with Session(_engine()) as session:
        _masters(session)
        batch = _add_batch(
            session,
            "purchase_transactions",
            [_purchase_values()],
            status="staged",
        )
        actor = ensure_app_user(session, username="brad", display_name="Brad")

        approval = approve_transaction_batches(
            session,
            actor=actor,
            source_types=("purchase_transactions",),
        )
        assert approval.approved_batch_ids == (batch.import_batch_id,)
        assert batch.status == "approved"

        preview = promote_transaction_batches(
            session,
            commit=False,
            source_types=("purchase_transactions",),
        )
        assert tuple(change.source_type for change in preview.changes) == (
            "purchase_transactions",
        )
        assert preview.lines_created == 1
        assert session.scalar(select(func.count(PurchaseLine.purchase_line_id))) == 0

        committed = promote_transaction_batches(
            session,
            commit=True,
            actor=actor,
            source_types=("purchase_transactions",),
        )
        assert committed.committed_batch_ids == (batch.import_batch_id,)
        assert session.scalar(select(func.count(PurchaseLine.purchase_line_id))) == 1
        assert session.scalar(select(func.count(SalesLine.sales_line_id))) == 0
        assert session.scalar(select(func.count(CoverOrderLine.cover_order_line_id))) == 0
        assert batch.status == "committed"


def test_subset_rejects_missing_requested_source() -> None:
    with Session(_engine()) as session:
        _masters(session)
        _add_batch(
            session,
            "purchase_transactions",
            [_purchase_values()],
            status="staged",
        )
        actor = ensure_app_user(session, username="brad", display_name="Brad")

        with pytest.raises(TransactionImportError, match="no eligible sales_transactions"):
            approve_transaction_batches(
                session,
                actor=actor,
                source_types=("sales_transactions",),
            )

