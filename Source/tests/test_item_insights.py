from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    CustomerAccount,
    ImportBatch,
    ImportRow,
    Item,
    SalesDocument,
    SalesLine,
)
from windsor_widget.services.item_insights import (
    build_monthly_sales_chart,
    get_item_customer_sales,
)
from windsor_widget.services.reporting import MonthlySalesPoint


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_customer_sales(session: Session) -> None:
    item = Item(
        item_number="I1",
        item_name="Item A",
        normalized_name="item a",
        is_sold=True,
        is_active=True,
    )
    customer_a = CustomerAccount(
        myob_record_id="C1",
        myob_card_id="CARD1",
        display_name="Customer A",
        normalized_name="customer a",
        city="Melbourne",
        state="VIC",
        is_active=True,
    )
    customer_b = CustomerAccount(
        myob_record_id="C2",
        myob_card_id="CARD2",
        display_name="Customer B",
        normalized_name="customer b",
        city="Geelong",
        state="VIC",
        is_active=True,
    )
    batch = ImportBatch(
        source_type="sales_transactions",
        source_file_name="salesdata.TXT",
        file_sha256="s" * 64,
        status="committed",
        row_count=4,
        accepted_row_count=4,
        committed_at=datetime(2026, 7, 21),
    )
    session.add_all([item, customer_a, customer_b, batch])
    session.flush()

    rows = []
    for index in range(1, 5):
        row = ImportRow(
            import_batch_id=batch.import_batch_id,
            row_number=index,
            raw_text="row",
            raw_json='{"values": {}}',
            natural_key=f"sale-{index}",
            row_sha256=f"sale-{index}".ljust(64, "0")[:64],
            status="committed",
            issue_count=0,
        )
        rows.append(row)
    session.add_all(rows)
    session.flush()

    document_specs = (
        (customer_a, "A-OLD", date(2025, 12, 15)),
        (customer_a, "A-NEW", date(2026, 5, 10)),
        (customer_b, "B-NEW", date(2026, 6, 10)),
        (customer_b, "B-ORDER", date(2026, 7, 10)),
    )
    documents = []
    for customer, invoice, transaction_date in document_specs:
        document = SalesDocument(
            customer_account_id=customer.customer_account_id,
            myob_customer_record_id=customer.myob_record_id,
            invoice_no=invoice,
            first_transaction_date=transaction_date,
            last_transaction_date=transaction_date,
            line_count=1,
            first_import_batch_id=batch.import_batch_id,
            last_import_batch_id=batch.import_batch_id,
        )
        documents.append(document)
    session.add_all(documents)
    session.flush()

    line_specs = (
        (documents[0], rows[0], customer_a, Decimal("5"), "I"),
        (documents[1], rows[1], customer_a, Decimal("10"), "I"),
        (documents[2], rows[2], customer_b, Decimal("3"), "I"),
        (documents[3], rows[3], customer_b, Decimal("100"), "O"),
    )
    for document, row, customer, quantity, sale_status in line_specs:
        session.add(
            SalesLine(
                sales_document_id=document.sales_document_id,
                item_id=item.item_id,
                line_sequence=1,
                source_import_row_id=row.import_row_id,
                source_row_sha256=row.row_sha256,
                last_import_batch_id=batch.import_batch_id,
                myob_item_number=item.item_number,
                customer_name_snapshot=customer.display_name,
                transaction_date=document.last_transaction_date,
                quantity=quantity,
                unit_price=Decimal("10"),
                line_total=quantity * Decimal("10"),
                sale_status=sale_status,
                is_active=True,
            )
        )
    session.commit()


def test_monthly_quantity_chart_builds_bar_points_and_linear_trend() -> None:
    points = (
        MonthlySalesPoint(date(2026, 1, 1), Decimal("10"), Decimal("100")),
        MonthlySalesPoint(date(2026, 2, 1), Decimal("20"), Decimal("200")),
        MonthlySalesPoint(date(2026, 3, 1), Decimal("30"), Decimal("300")),
    )

    chart = build_monthly_sales_chart(points)

    assert chart.total_quantity == Decimal("60")
    assert chart.average_quantity == Decimal("20")
    assert chart.monthly_slope == Decimal("10")
    assert chart.trend_start == Decimal("10")
    assert chart.trend_end == Decimal("30")
    assert len(chart.points) == 3
    assert all(point.bar_height > 0 for point in chart.points)
    assert chart.trend_points.startswith(f"{chart.points[0].x:.2f},")
    assert len(chart.ticks) == 5


def test_item_customer_sales_lists_period_and_all_time_invoiced_quantities() -> None:
    with Session(_engine()) as session:
        _seed_customer_sales(session)
        rows = get_item_customer_sales(
            session,
            "I1",
            period_start=date(2026, 1, 1),
            as_of_date=date(2026, 7, 31),
        )

    assert [row.display_name for row in rows] == ["Customer A", "Customer B"]
    assert rows[0].period_quantity == Decimal("10")
    assert rows[0].all_time_quantity == Decimal("15")
    assert rows[0].period_invoice_count == 1
    assert rows[0].all_time_invoice_count == 2
    assert rows[0].last_purchase_date == date(2026, 5, 10)

    assert rows[1].period_quantity == Decimal("3")
    assert rows[1].all_time_quantity == Decimal("3")
    assert rows[1].all_time_invoice_count == 1
    # The open order with status O is deliberately excluded.
    assert rows[1].all_time_quantity != Decimal("103")
