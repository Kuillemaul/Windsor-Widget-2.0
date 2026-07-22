from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

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
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    PurchaseDocument,
    PurchaseLine,
    SalesDocument,
    SalesLine,
    Supplier,
)
from windsor_widget.services.planning import (
    get_item_planning_analysis,
    get_order_analysis,
    get_planning_readiness,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _batch(
    session: Session, source_type: str, count: int, marker: str
) -> tuple[ImportBatch, list[ImportRow]]:
    batch = ImportBatch(
        source_type=source_type,
        source_file_name=f"{marker}.TXT",
        file_sha256=marker.ljust(64, marker[0])[:64],
        status="committed",
        row_count=count,
        accepted_row_count=count,
        committed_at=datetime(2026, 7, 21),
    )
    session.add(batch)
    session.flush()
    rows = [
        ImportRow(
            import_batch_id=batch.import_batch_id,
            row_number=index,
            raw_text="row",
            raw_json='{"values": {}}',
            natural_key=f"{marker}-{index}",
            row_sha256=f"{marker}-{index}".ljust(64, "0")[:64],
            status="committed",
            issue_count=0,
        )
        for index in range(1, count + 1)
    ]
    session.add_all(rows)
    session.flush()
    return batch, rows


def _seed(session: Session) -> None:
    actor = AppUser(username="brad", display_name="Brad Mayze")
    customer = CustomerAccount(
        myob_record_id="C1",
        display_name="Customer A",
        normalized_name="customer a",
        is_active=True,
    )
    supplier = Supplier(
        myob_record_id="S1",
        display_name="Supplier A",
        normalized_name="supplier a",
        is_active=True,
    )
    item = Item(
        item_number="I1",
        item_name="Item One",
        normalized_name="item one",
        is_active=True,
        is_inventoried=True,
        is_bought=True,
        is_sold=True,
        reorder_quantity=Decimal("10"),
        minimum_level=Decimal("5"),
    )
    session.add_all([actor, customer, supplier, item])
    session.flush()

    sales_batch, sales_rows = _batch(session, "sales_transactions", 6, "sales")
    sales_document = SalesDocument(
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="INV1",
        first_transaction_date=date(2026, 1, 15),
        last_transaction_date=date(2026, 6, 15),
        line_count=6,
        first_import_batch_id=sales_batch.import_batch_id,
        last_import_batch_id=sales_batch.import_batch_id,
    )
    session.add(sales_document)
    session.flush()
    quantities = [10, 10, 10, 20, 20, 20]
    for index, quantity in enumerate(quantities, start=1):
        session.add(
            SalesLine(
                sales_document_id=sales_document.sales_document_id,
                item_id=item.item_id,
                line_sequence=index,
                source_import_row_id=sales_rows[index - 1].import_row_id,
                source_row_sha256=sales_rows[index - 1].row_sha256,
                last_import_batch_id=sales_batch.import_batch_id,
                myob_item_number="I1",
                customer_name_snapshot="Customer A",
                transaction_date=date(2026, index, 15),
                quantity=Decimal(quantity),
                unit_price=Decimal("2"),
                line_total=Decimal(quantity * 2),
                is_active=True,
            )
        )

    purchase_batch, purchase_rows = _batch(
        session, "purchase_transactions", 1, "purchase"
    )
    purchase_document = PurchaseDocument(
        supplier_id=supplier.supplier_id,
        myob_supplier_record_id="S1",
        purchase_no="PO1",
        first_transaction_date=date(2026, 5, 1),
        last_transaction_date=date(2026, 5, 1),
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
            source_import_row_id=purchase_rows[0].import_row_id,
            source_row_sha256=purchase_rows[0].row_sha256,
            last_import_batch_id=purchase_batch.import_batch_id,
            myob_item_number="I1",
            supplier_name_snapshot="Supplier A",
            transaction_date=date(2026, 5, 1),
            quantity=Decimal("100"),
            unit_price=Decimal("1.50"),
            line_total=Decimal("150"),
            currency_code="AUD",
            is_active=True,
        )
    )

    cover_batch, cover_rows = _batch(
        session, "cover_order_snapshot", 1, "cover"
    )
    cover_snapshot = CoverOrderSnapshot(
        import_batch_id=cover_batch.import_batch_id,
        captured_at=datetime(2026, 7, 20),
        source_file_name="cover.TXT",
        document_count=1,
        row_count=1,
        is_current=True,
        committed_by_user_id=actor.user_id,
    )
    session.add(cover_snapshot)
    session.flush()
    cover_document = CoverOrderDocument(
        cover_order_snapshot_id=cover_snapshot.cover_order_snapshot_id,
        customer_account_id=customer.customer_account_id,
        myob_customer_record_id="C1",
        invoice_no="ORD1",
        first_transaction_date=date(2026, 7, 1),
        last_transaction_date=date(2026, 7, 1),
        line_count=1,
    )
    session.add(cover_document)
    session.flush()
    session.add(
        CoverOrderLine(
            cover_order_document_id=cover_document.cover_order_document_id,
            item_id=item.item_id,
            line_sequence=1,
            source_import_row_id=cover_rows[0].import_row_id,
            source_row_sha256=cover_rows[0].row_sha256,
            myob_item_number="I1",
            customer_name_snapshot="Customer A",
            transaction_date=date(2026, 7, 1),
            quantity=Decimal("25"),
            unit_price=Decimal("2"),
            line_total=Decimal("50"),
        )
    )

    inventory_snapshot = InventorySnapshot(
        captured_at=datetime(2026, 7, 20),
        source_file_name="zinvs1.xlsx",
        source_sha256="i" * 64,
        row_count=1,
        is_current=True,
        committed_by_user_id=actor.user_id,
    )
    session.add(inventory_snapshot)
    session.flush()
    session.add(
        InventorySnapshotLine(
            inventory_snapshot_id=inventory_snapshot.inventory_snapshot_id,
            item_id=item.item_id,
            source_row_number=12,
            item_number_snapshot="I1",
            item_name_snapshot="Item One",
            on_hand=Decimal("40"),
            committed=Decimal("30"),
            on_order=Decimal("10"),
            available=Decimal("20"),
        )
    )
    session.commit()


def test_item_planning_uses_completed_months_and_available_stock() -> None:
    with Session(_engine()) as session:
        _seed(session)
        analysis = get_item_planning_analysis(
            session,
            "I1",
            analysis_months=6,
            fallback_lead_weeks=14,
            trend_mode="3v3",
            as_of_date=date(2026, 7, 22),
        )

        assert analysis.analysis_start == date(2026, 1, 1)
        assert analysis.analysis_end == date(2026, 6, 30)
        assert analysis.sales_quantity == Decimal("90")
        assert analysis.average_monthly_sales == Decimal("15")
        assert analysis.inventory is not None
        assert analysis.inventory.available == Decimal("20")
        assert analysis.current_cover_quantity == Decimal("25")
        assert analysis.cover_committed_delta == Decimal("-5")
        assert analysis.lead_days == 98
        assert analysis.lead_time_source == "fallback 98 days"
        assert analysis.suggested_order == Decimal("30")
        assert analysis.trend.current_total == Decimal("60")
        assert analysis.trend.previous_total == Decimal("30")
        assert analysis.trend.significant is True
        assert analysis.adjusted_suggested_order == Decimal("70")
        assert analysis.status == "order"
        assert analysis.latest_purchase is not None
        assert analysis.latest_purchase.supplier_name == "Supplier A"
        assert any("Dated inbound" in gap for gap in analysis.data_gaps)


def test_order_analysis_and_readiness_are_ui_ready() -> None:
    with Session(_engine()) as session:
        _seed(session)
        result = get_order_analysis(
            session,
            analysis_months=6,
            fallback_lead_weeks=14,
            trend_mode="3v3",
            as_of_date=date(2026, 7, 22),
            limit=10,
        )
        assert result.considered_items == 1
        assert result.flagged_items == 1
        assert len(result.rows) == 1
        assert result.rows[0].item_number == "I1"
        assert result.rows[0].status == "order"
        assert result.rows[0].adjusted_suggested_order == Decimal("70")

        readiness = get_planning_readiness(session)
        assert readiness.inventory_row_count == 1
        assert readiness.active_inventoried_items == 1
        assert readiness.active_inventoried_items_missing_snapshot == 0
        assert readiness.current_cover_order_snapshots == 1
        assert any("Preferred item-supplier" in gap for gap in readiness.gaps)
