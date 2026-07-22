from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    CoverOrderDocument,
    CoverOrderLine,
    CoverOrderSnapshot,
    CustomerAccount,
    ImportBatch,
    ImportRow,
    Item,
    PurchaseDocument,
    PurchaseLine,
    SalesDocument,
    SalesLine,
    Supplier,
    TransactionLineObservation,
)
from windsor_widget.services.reporting import (
    ReportingLookupError,
    get_customer_monthly_sales,
    get_customer_summary,
    get_foundation_counts,
    get_item_monthly_sales,
    get_item_summary,
    period_start_for_months,
    search_customers,
    search_items,
    validate_foundation_counts,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed(session: Session) -> None:
    actor = AppUser(username="brad", display_name="Brad Mayze")
    customer = CustomerAccount(
        myob_record_id="C1",
        myob_card_id="CUS1",
        display_name="Customer A",
        normalized_name="customer a",
        city="Melbourne",
        state="VIC",
        is_active=True,
    )
    inactive_customer = CustomerAccount(
        myob_record_id="C2",
        display_name="Dormant Customer",
        normalized_name="dormant customer",
        is_active=False,
    )
    supplier = Supplier(
        myob_record_id="S1",
        display_name="Supplier A",
        normalized_name="supplier a",
        is_active=True,
    )
    item = Item(
        item_number="I1",
        item_name="Item A",
        normalized_name="item a",
        is_bought=True,
        is_sold=True,
        is_inventoried=True,
        reorder_quantity=Decimal("25"),
        minimum_level=Decimal("10"),
        standard_cost=Decimal("2.50"),
        replenishment_policy="stocked",
        policy_source="user",
        is_active=True,
    )
    excluded_item = Item(
        item_number="X1",
        item_name="Excluded Item",
        normalized_name="excluded item",
        excluded_from_item_view=True,
        is_active=True,
    )
    session.add_all(
        [actor, customer, inactive_customer, supplier, item, excluded_item]
    )
    session.flush()

    sales_batch = ImportBatch(
        source_type="sales_transactions",
        source_file_name="salesdata.TXT",
        file_sha256="s" * 64,
        status="committed",
        row_count=2,
        accepted_row_count=2,
        committed_at=datetime(2026, 7, 21),
    )
    cover_batch = ImportBatch(
        source_type="cover_order_snapshot",
        source_file_name="cover.TXT",
        file_sha256="c" * 64,
        status="committed",
        row_count=2,
        accepted_row_count=2,
        committed_at=datetime(2026, 7, 21),
    )
    purchase_batch = ImportBatch(
        source_type="purchase_transactions",
        source_file_name="purchase.TXT",
        file_sha256="p" * 64,
        status="committed",
        row_count=1,
        accepted_row_count=1,
        committed_at=datetime(2026, 7, 21),
    )
    session.add_all([sales_batch, cover_batch, purchase_batch])
    session.flush()

    rows: list[ImportRow] = []
    for batch, count, prefix in (
        (sales_batch, 2, "sales"),
        (cover_batch, 2, "cover"),
        (purchase_batch, 1, "purchase"),
    ):
        for number in range(1, count + 1):
            rows.append(
                ImportRow(
                    import_batch_id=batch.import_batch_id,
                    row_number=number,
                    raw_text="row",
                    raw_json='{"values": {}}',
                    natural_key=f"{prefix}-{number}",
                    row_sha256=f"{prefix}-{number}".ljust(64, "0")[:64],
                    status="committed",
                    issue_count=0,
                )
            )
    session.add_all(rows)
    session.flush()
    sales_rows = [row for row in rows if row.import_batch_id == sales_batch.import_batch_id]
    cover_rows = [row for row in rows if row.import_batch_id == cover_batch.import_batch_id]
    purchase_row = next(
        row for row in rows if row.import_batch_id == purchase_batch.import_batch_id
    )

    sales_document = SalesDocument(
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="INV1",
        first_transaction_date=date(2026, 5, 10),
        last_transaction_date=date(2026, 7, 10),
        line_count=2,
        first_import_batch_id=sales_batch.import_batch_id,
        last_import_batch_id=sales_batch.import_batch_id,
    )
    purchase_document = PurchaseDocument(
        supplier_id=supplier.supplier_id,
        myob_supplier_record_id="S1",
        purchase_no="PO1",
        first_transaction_date=date(2026, 6, 1),
        last_transaction_date=date(2026, 6, 1),
        line_count=1,
        first_import_batch_id=purchase_batch.import_batch_id,
        last_import_batch_id=purchase_batch.import_batch_id,
    )
    session.add_all([sales_document, purchase_document])
    session.flush()

    sales_lines = [
        SalesLine(
            sales_document_id=sales_document.sales_document_id,
            item_id=item.item_id,
            line_sequence=1,
            source_import_row_id=sales_rows[0].import_row_id,
            source_row_sha256=sales_rows[0].row_sha256,
            last_import_batch_id=sales_batch.import_batch_id,
            myob_item_number="I1",
            customer_name_snapshot="Customer A",
            sale_status="I",
            transaction_date=date(2026, 5, 10),
            quantity=Decimal("2"),
            unit_price=Decimal("5"),
            line_total=Decimal("10"),
            is_active=True,
        ),
        SalesLine(
            sales_document_id=sales_document.sales_document_id,
            item_id=item.item_id,
            line_sequence=2,
            source_import_row_id=sales_rows[1].import_row_id,
            source_row_sha256=sales_rows[1].row_sha256,
            last_import_batch_id=sales_batch.import_batch_id,
            myob_item_number="I1",
            customer_name_snapshot="Customer A",
            sale_status="I",
            transaction_date=date(2026, 7, 10),
            quantity=Decimal("3"),
            unit_price=Decimal("5"),
            line_total=Decimal("15"),
            is_active=True,
        ),
    ]
    purchase_line = PurchaseLine(
        purchase_document_id=purchase_document.purchase_document_id,
        item_id=item.item_id,
        line_sequence=1,
        source_import_row_id=purchase_row.import_row_id,
        source_row_sha256=purchase_row.row_sha256,
        last_import_batch_id=purchase_batch.import_batch_id,
        myob_item_number="I1",
        supplier_name_snapshot="Supplier A",
        transaction_date=date(2026, 6, 1),
        quantity=Decimal("20"),
        unit_price=Decimal("2.50"),
        line_total=Decimal("50"),
        is_active=True,
    )
    session.add_all([*sales_lines, purchase_line])
    session.flush()

    old_snapshot = CoverOrderSnapshot(
        import_batch_id=cover_batch.import_batch_id,
        captured_at=datetime(2026, 6, 1),
        source_file_name="old-cover.TXT",
        document_count=1,
        row_count=1,
        is_current=False,
        committed_by_user_id=actor.user_id,
    )
    session.add(old_snapshot)
    session.flush()
    # The immutable current snapshot needs a distinct batch identity.
    current_batch = ImportBatch(
        source_type="cover_order_snapshot",
        source_file_name="current-cover.TXT",
        file_sha256="n" * 64,
        status="committed",
        row_count=1,
        accepted_row_count=1,
        committed_at=datetime(2026, 7, 21),
    )
    session.add(current_batch)
    session.flush()
    current_row = ImportRow(
        import_batch_id=current_batch.import_batch_id,
        row_number=1,
        raw_text="row",
        raw_json='{"values": {}}',
        natural_key="current-cover-1",
        row_sha256="current-cover-1".ljust(64, "0")[:64],
        status="committed",
        issue_count=0,
    )
    current_snapshot = CoverOrderSnapshot(
        import_batch_id=current_batch.import_batch_id,
        captured_at=datetime(2026, 7, 21),
        source_file_name="current-cover.TXT",
        document_count=1,
        row_count=1,
        is_current=True,
        committed_by_user_id=actor.user_id,
    )
    session.add_all([current_row, current_snapshot])
    session.flush()

    old_document = CoverOrderDocument(
        cover_order_snapshot_id=old_snapshot.cover_order_snapshot_id,
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="OLD1",
        first_transaction_date=date(2026, 6, 1),
        last_transaction_date=date(2026, 6, 1),
        line_count=1,
    )
    current_document = CoverOrderDocument(
        cover_order_snapshot_id=current_snapshot.cover_order_snapshot_id,
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="ORD1",
        first_transaction_date=date(2026, 7, 20),
        last_transaction_date=date(2026, 7, 20),
        line_count=1,
    )
    session.add_all([old_document, current_document])
    session.flush()
    old_cover_line = CoverOrderLine(
        cover_order_document_id=old_document.cover_order_document_id,
        item_id=item.item_id,
        line_sequence=1,
        source_import_row_id=cover_rows[0].import_row_id,
        source_row_sha256=cover_rows[0].row_sha256,
        myob_item_number="I1",
        customer_name_snapshot="Customer A",
        transaction_date=date(2026, 6, 1),
        quantity=Decimal("99"),
        unit_price=Decimal("5"),
        line_total=Decimal("495"),
        is_cover_order=True,
    )
    current_cover_line = CoverOrderLine(
        cover_order_document_id=current_document.cover_order_document_id,
        item_id=item.item_id,
        line_sequence=1,
        source_import_row_id=current_row.import_row_id,
        source_row_sha256=current_row.row_sha256,
        myob_item_number="I1",
        customer_name_snapshot="Customer A",
        transaction_date=date(2026, 7, 20),
        quantity=Decimal("4"),
        unit_price=Decimal("5"),
        line_total=Decimal("20"),
        is_cover_order=True,
    )
    session.add_all([old_cover_line, current_cover_line])
    session.flush()

    for entity_type, entity_id, row, batch in (
        ("sales_line", sales_lines[0].sales_line_id, sales_rows[0], sales_batch),
        ("sales_line", sales_lines[1].sales_line_id, sales_rows[1], sales_batch),
        ("cover_order_line", old_cover_line.cover_order_line_id, cover_rows[0], cover_batch),
        ("cover_order_line", current_cover_line.cover_order_line_id, current_row, current_batch),
        ("purchase_line", purchase_line.purchase_line_id, purchase_row, purchase_batch),
    ):
        session.add(
            TransactionLineObservation(
                source_type=(
                    "cover_order_snapshot"
                    if entity_type == "cover_order_line"
                    else "purchase_transactions"
                    if entity_type == "purchase_line"
                    else "sales_transactions"
                ),
                entity_type=entity_type,
                entity_id=entity_id,
                import_batch_id=batch.import_batch_id,
                import_row_id=row.import_row_id,
                action="created",
            )
        )
    session.commit()


def test_period_start_uses_inclusive_calendar_months() -> None:
    assert period_start_for_months(date(2026, 7, 22), 12) == date(2025, 8, 1)
    assert period_start_for_months(date(2026, 1, 2), 3) == date(2025, 11, 1)
    with pytest.raises(ValueError):
        period_start_for_months(date(2026, 7, 22), 0)


def test_foundation_counts_and_summary_totals() -> None:
    with Session(_engine()) as session:
        _seed(session)
        counts = get_foundation_counts(session)
        assert counts.items == 2
        assert counts.customer_accounts == 2
        assert counts.suppliers == 1
        assert counts.sales_documents == 1
        assert counts.sales_lines == 2
        assert counts.current_cover_order_snapshots == 1
        assert counts.purchase_documents == 1
        assert counts.purchase_lines == 1
        assert validate_foundation_counts(counts) == ()

        item = get_item_summary(
            session,
            "I1",
            months=3,
            as_of_date=date(2026, 7, 22),
        )
        assert item.period_start == date(2026, 5, 1)
        assert item.sales_all_time.quantity == Decimal("5")
        assert item.sales_all_time.value == Decimal("25")
        assert item.current_cover_orders.quantity == Decimal("4")
        assert item.current_cover_orders.value == Decimal("20")
        assert item.cover_snapshot_captured_at == datetime(2026, 7, 21)
        assert item.purchases_all_time.quantity == Decimal("20")
        assert item.purchases_all_time.value == Decimal("50")

        customer = get_customer_summary(
            session,
            "C1",
            months=3,
            as_of_date=date(2026, 7, 22),
        )
        assert customer.sales_all_time.document_count == 1
        assert customer.sales_all_time.line_count == 2
        assert customer.sales_all_time.value == Decimal("25")
        assert customer.current_cover_orders.document_count == 1
        assert customer.current_cover_orders.quantity == Decimal("4")
        assert customer.cover_snapshot_captured_at == datetime(2026, 7, 21)


def test_monthly_sales_are_zero_filled() -> None:
    with Session(_engine()) as session:
        _seed(session)
        item_points = get_item_monthly_sales(
            session,
            "I1",
            months=3,
            as_of_date=date(2026, 7, 22),
        )
        assert [point.month_start for point in item_points] == [
            date(2026, 5, 1),
            date(2026, 6, 1),
            date(2026, 7, 1),
        ]
        assert [point.value for point in item_points] == [
            Decimal("10"),
            Decimal("0"),
            Decimal("15"),
        ]

        customer_points = get_customer_monthly_sales(
            session,
            "C1",
            months=3,
            as_of_date=date(2026, 7, 22),
        )
        assert [point.quantity for point in customer_points] == [
            Decimal("2"),
            Decimal("0"),
            Decimal("3"),
        ]


def test_search_defaults_hide_inactive_and_excluded_records() -> None:
    with Session(_engine()) as session:
        _seed(session)
        assert [row.item_number for row in search_items(session, "item")] == ["I1"]
        assert [row.myob_record_id for row in search_customers(session, "customer")] == [
            "C1"
        ]


def test_exact_lookup_failure_is_explicit() -> None:
    with Session(_engine()) as session:
        _seed(session)
        with pytest.raises(ReportingLookupError, match="No item"):
            get_item_summary(session, "MISSING")
        with pytest.raises(ReportingLookupError, match="No customer"):
            get_customer_summary(session, "MISSING")
