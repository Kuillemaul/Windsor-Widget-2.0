from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    CustomerAccount,
    ImportBatch,
    ImportRow,
    Item,
    SalesDocument,
    SalesLine,
)
from windsor_widget.services.customer_insights import (
    get_customer_invoice_detail,
    get_customer_invoices,
    get_customer_item_sales,
    list_customers,
    set_customer_commercial_terms,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed(session: Session):
    actor = AppUser(username="brad", display_name="Brad")
    customer = CustomerAccount(
        myob_record_id="C1",
        myob_card_id="CARD1",
        display_name="Customer A",
        normalized_name="customer a",
        city="Melbourne",
        state="VIC",
        payment_basis="unknown",
        freight_payer="unknown",
        is_active=True,
    )
    item_a = Item(item_number="I1", item_name="Item A", normalized_name="item a", is_active=True)
    item_b = Item(item_number="I2", item_name="Item B", normalized_name="item b", is_active=True)
    batch = ImportBatch(
        source_type="sales_transactions",
        source_file_name="sales.TXT",
        file_sha256="x" * 64,
        status="committed",
        row_count=4,
        accepted_row_count=4,
        committed_at=datetime(2026, 7, 1),
    )
    session.add_all([actor, customer, item_a, item_b, batch])
    session.flush()

    rows = []
    for index in range(4):
        row = ImportRow(
            import_batch_id=batch.import_batch_id,
            row_number=index + 1,
            raw_text="row",
            raw_json='{"values": {}}',
            natural_key=f"row-{index}",
            row_sha256=f"row-{index}".ljust(64, "0")[:64],
            status="committed",
            issue_count=0,
        )
        rows.append(row)
    session.add_all(rows)
    session.flush()

    doc1 = SalesDocument(
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="INV1",
        first_transaction_date=date(2025, 12, 1),
        last_transaction_date=date(2025, 12, 1),
        line_count=1,
        first_import_batch_id=batch.import_batch_id,
        last_import_batch_id=batch.import_batch_id,
    )
    doc2 = SalesDocument(
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="INV2",
        first_transaction_date=date(2026, 6, 1),
        last_transaction_date=date(2026, 6, 1),
        line_count=2,
        first_import_batch_id=batch.import_batch_id,
        last_import_batch_id=batch.import_batch_id,
    )
    doc3 = SalesDocument(
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="ORD1",
        first_transaction_date=date(2026, 7, 1),
        last_transaction_date=date(2026, 7, 1),
        line_count=1,
        first_import_batch_id=batch.import_batch_id,
        last_import_batch_id=batch.import_batch_id,
    )
    session.add_all([doc1, doc2, doc3])
    session.flush()

    specs = (
        (doc1, rows[0], item_a, Decimal("5"), Decimal("8"), None, "I"),
        (doc2, rows[1], item_a, Decimal("10"), Decimal("12"), Decimal("10"), "I"),
        (doc2, rows[2], item_b, Decimal("3"), Decimal("20"), None, "I"),
        (doc3, rows[3], item_a, Decimal("100"), Decimal("99"), None, "O"),
    )
    for sequence, (doc, row, item, qty, price, discount, status) in enumerate(specs, start=1):
        net = price * (Decimal("1") - ((discount or Decimal("0")) / Decimal("100")))
        session.add(
            SalesLine(
                sales_document_id=doc.sales_document_id,
                item_id=item.item_id,
                line_sequence=sequence if doc is doc2 else 1,
                source_import_row_id=row.import_row_id,
                source_row_sha256=row.row_sha256,
                last_import_batch_id=batch.import_batch_id,
                myob_item_number=item.item_number,
                customer_name_snapshot=customer.display_name,
                transaction_date=doc.last_transaction_date,
                quantity=qty,
                unit_price=price,
                discount_percent=discount,
                line_total=qty * net,
                sale_status=status,
                currency_code="AUD",
                is_active=True,
            )
        )
    session.commit()
    return actor, customer, item_a, item_b, doc2


def test_customer_item_history_price_and_invoice_drilldown():
    with Session(_engine()) as session:
        actor, customer, item_a, item_b, invoice = _seed(session)
        items = get_customer_item_sales(
            session,
            customer.customer_account_id,
            period_start=date(2026, 1, 1),
            as_of_date=date(2026, 7, 31),
        )
        assert [row.item_number for row in items] == ["I1", "I2"]
        assert items[0].period_quantity == Decimal("10")
        assert items[0].all_time_quantity == Decimal("15")
        assert items[0].last_invoice_no == "INV2"
        assert items[0].last_unit_price == Decimal("12")
        assert items[0].last_net_unit_price == Decimal("10.8")
        assert items[0].last_unit_price != Decimal("99")

        invoices = get_customer_invoices(
            session,
            customer.customer_account_id,
            as_of_date=date(2026, 7, 31),
        )
        assert [row.invoice_no for row in invoices] == ["INV2", "INV1"]

        detail = get_customer_invoice_detail(
            session,
            customer.customer_account_id,
            invoice.sales_document_id,
        )
        assert detail.invoice_no == "INV2"
        assert len(detail.lines) == 2
        assert detail.quantity == Decimal("13")


def test_customer_search_and_audited_commercial_settings():
    with Session(_engine()) as session:
        actor, customer, *_ = _seed(session)
        rows = list_customers(session, query="Melbourne", state="VIC")
        assert [row.display_name for row in rows] == ["Customer A"]

        set_customer_commercial_terms(
            session,
            customer_account_id=customer.customer_account_id,
            payment_basis="account",
            freight_payer="windsor",
            actor_user_id=actor.user_id,
        )
        session.commit()
        session.refresh(customer)
        assert customer.payment_basis == "account"
        assert customer.freight_payer == "windsor"
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "customer.commercial_terms.updated"
            )
        )
        assert event is not None
        assert '"payment_basis": "unknown"' in event.before_json
        assert '"payment_basis": "account"' in event.after_json
