from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    CustomerAccount,
    ImportBatch,
    ImportRow,
    Item,
    PurchaseDocument,
    PurchaseLine,
    SalesDocument,
    SalesLine,
    Supplier,
)
from windsor_widget.services.customer_insights import list_customers
from windsor_widget.services.item_policy import set_item_policies
from windsor_widget.services.supplier_insights import list_suppliers


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _batch(
    session: Session,
    *,
    source_type: str,
    source_file_name: str,
    marker: str,
) -> tuple[ImportBatch, ImportRow]:
    batch = ImportBatch(
        source_type=source_type,
        source_file_name=source_file_name,
        file_sha256=marker.ljust(64, "0")[:64],
        status="committed",
        row_count=1,
        accepted_row_count=1,
        committed_at=datetime(2026, 7, 23),
    )
    session.add(batch)
    session.flush()
    row = ImportRow(
        import_batch_id=batch.import_batch_id,
        row_number=1,
        raw_text="row",
        raw_json='{"values": {}}',
        natural_key=marker,
        row_sha256=(marker + "-row").ljust(64, "0")[:64],
        status="committed",
        issue_count=0,
    )
    session.add(row)
    session.flush()
    return batch, row


def test_default_registers_hide_cards_without_activity_but_search_finds_them():
    with Session(_engine()) as session:
        billed_customer = CustomerAccount(
            myob_record_id="C1",
            display_name="Invoice Customer",
            normalized_name="invoice customer",
            is_active=True,
        )
        hidden_customer = CustomerAccount(
            myob_record_id="C2",
            display_name="Rest Super Customer",
            normalized_name="rest super customer",
            is_active=True,
        )
        billed_supplier = Supplier(
            myob_record_id="S1",
            display_name="Bill Supplier",
            normalized_name="bill supplier",
            is_active=True,
        )
        hidden_supplier = Supplier(
            myob_record_id="S2",
            display_name="Rest Super",
            normalized_name="rest super",
            is_active=True,
        )
        item = Item(
            item_number="I1",
            item_name="Item One",
            normalized_name="item one",
            is_active=True,
        )
        session.add_all(
            [
                billed_customer,
                hidden_customer,
                billed_supplier,
                hidden_supplier,
                item,
            ]
        )
        session.flush()

        sales_batch, sales_row = _batch(
            session,
            source_type="sales_transactions",
            source_file_name="ITEMSALES.TXT",
            marker="sales",
        )
        sales_document = SalesDocument(
            customer_account_id=billed_customer.customer_account_id,
            myob_customer_record_id="C1",
            invoice_no="INV1",
            first_transaction_date=date(2026, 7, 1),
            last_transaction_date=date(2026, 7, 1),
            line_count=1,
            first_import_batch_id=sales_batch.import_batch_id,
            last_import_batch_id=sales_batch.import_batch_id,
        )
        session.add(sales_document)
        session.flush()
        session.add(
            SalesLine(
                sales_document_id=sales_document.sales_document_id,
                item_id=item.item_id,
                line_sequence=1,
                source_import_row_id=sales_row.import_row_id,
                source_row_sha256=sales_row.row_sha256,
                last_import_batch_id=sales_batch.import_batch_id,
                myob_item_number=item.item_number,
                customer_name_snapshot=billed_customer.display_name,
                transaction_date=date(2026, 7, 1),
                quantity=Decimal("1"),
                unit_price=Decimal("10"),
                line_total=Decimal("10"),
                sale_status="I",
                is_active=True,
            )
        )

        purchase_batch, purchase_row = _batch(
            session,
            source_type="purchase_transactions",
            source_file_name="ITEMPURbills.TXT",
            marker="purchase",
        )
        purchase_document = PurchaseDocument(
            supplier_id=billed_supplier.supplier_id,
            myob_supplier_record_id="S1",
            purchase_no="BILL1",
            first_transaction_date=date(2026, 7, 2),
            last_transaction_date=date(2026, 7, 2),
            line_count=1,
            first_import_batch_id=purchase_batch.import_batch_id,
            last_import_batch_id=purchase_batch.import_batch_id,
        )
        session.add(purchase_document)
        session.flush()
        session.add(
            PurchaseLine(
                purchase_document_id=purchase_document.purchase_document_id,
                item_id=item.item_id,
                line_sequence=1,
                source_import_row_id=purchase_row.import_row_id,
                source_row_sha256=purchase_row.row_sha256,
                last_import_batch_id=purchase_batch.import_batch_id,
                myob_item_number=item.item_number,
                supplier_name_snapshot=billed_supplier.display_name,
                transaction_date=date(2026, 7, 2),
                quantity=Decimal("1"),
                unit_price=Decimal("5"),
                line_total=Decimal("5"),
                purchase_status="B",
                currency_code="AUD",
                is_active=True,
            )
        )
        session.commit()

        assert [row.display_name for row in list_customers(session)] == [
            "Invoice Customer"
        ]
        assert [row.display_name for row in list_customers(session, query="rest")] == [
            "Rest Super Customer"
        ]

        assert [row.display_name for row in list_suppliers(session)] == [
            "Bill Supplier"
        ]
        assert [row.display_name for row in list_suppliers(session, query="res")] == [
            "Rest Super"
        ]


def test_bulk_item_policy_change_is_audited_per_changed_item():
    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        first = Item(
            item_number="I1",
            item_name="Item One",
            normalized_name="item one",
            replenishment_policy="unknown",
            is_active=True,
        )
        second = Item(
            item_number="I2",
            item_name="Item Two",
            normalized_name="item two",
            replenishment_policy="stocked",
            is_active=True,
        )
        session.add_all([actor, first, second])
        session.flush()

        changed = set_item_policies(
            session,
            item_ids=(first.item_id, second.item_id),
            policy="manual",
            actor_user_id=actor.user_id,
        )
        session.commit()

        assert changed == 2
        assert first.replenishment_policy == "manual"
        assert second.replenishment_policy == "manual"
        assert (
            session.scalar(
                select(func.count(AuditEvent.audit_event_id)).where(
                    AuditEvent.action == "item.policy.updated"
                )
            )
            == 2
        )
