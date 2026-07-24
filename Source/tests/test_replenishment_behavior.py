from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    CustomerAccount,
    ImportBatch,
    ImportRow,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    ItemSupplier,
    PurchaseDocument,
    PurchaseLine,
    SalesDocument,
    SalesLine,
    Supplier,
)
from windsor_widget.services.replenishment_behavior import (
    get_item_replenishment_behavior,
    get_supplier_receiving_behavior,
)


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
    count: int,
) -> tuple[ImportBatch, list[ImportRow]]:
    batch = ImportBatch(
        source_type=source_type,
        source_file_name=source_file_name,
        file_sha256=marker.ljust(64, "0")[:64],
        status="committed",
        row_count=count,
        accepted_row_count=count,
        committed_at=datetime(2026, 7, 1),
    )
    session.add(batch)
    session.flush()

    rows: list[ImportRow] = []
    for index in range(1, count + 1):
        row = ImportRow(
            import_batch_id=batch.import_batch_id,
            row_number=index,
            raw_text="row",
            raw_json='{"values": {}}',
            natural_key=f"{marker}-{index}",
            row_sha256=f"{marker}-{index}".ljust(64, "0")[:64],
            status="committed",
            issue_count=0,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return batch, rows


def _seed(session: Session):
    actor = AppUser(username="brad", display_name="Brad")
    customer = CustomerAccount(
        myob_record_id="C1",
        display_name="Customer One",
        normalized_name="customer one",
        is_active=True,
    )
    supplier = Supplier(
        myob_record_id="3534",
        display_name="Yuchang Textile Factory",
        normalized_name="yuchang textile factory",
        default_manufacturing_lead_days=55,
        default_transit_lead_days=28,
        default_buffer_days=7,
        is_active=True,
    )
    item = Item(
        item_number="TEST-ITEM",
        item_name="Test Item",
        normalized_name="test item",
        replenishment_policy="stocked",
        is_active=True,
        is_bought=True,
        is_sold=True,
        is_inventoried=True,
    )
    session.add_all([actor, customer, supplier, item])
    session.flush()
    session.add(
        ItemSupplier(
            item_id=item.item_id,
            supplier_id=supplier.supplier_id,
            is_preferred=True,
            match_status="approved",
            match_method="user",
        )
    )

    purchase_batch, purchase_rows = _batch(
        session,
        source_type="purchase_transactions",
        source_file_name="ITEMPURbills.TXT",
        marker="purchase",
        count=4,
    )
    purchase_specs = (
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 1, 6), Decimal("50")),
        (date(2025, 7, 1), Decimal("120")),
        (date(2026, 1, 1), Decimal("110")),
    )
    for index, (transaction_date, quantity) in enumerate(purchase_specs, start=1):
        document = PurchaseDocument(
            supplier_id=supplier.supplier_id,
            myob_supplier_record_id=supplier.myob_record_id,
            purchase_no=f"BILL-{index}",
            first_transaction_date=transaction_date,
            last_transaction_date=transaction_date,
            line_count=1,
            first_import_batch_id=purchase_batch.import_batch_id,
            last_import_batch_id=purchase_batch.import_batch_id,
        )
        session.add(document)
        session.flush()
        session.add(
            PurchaseLine(
                purchase_document_id=document.purchase_document_id,
                item_id=item.item_id,
                line_sequence=1,
                source_import_row_id=purchase_rows[index - 1].import_row_id,
                source_row_sha256=purchase_rows[index - 1].row_sha256,
                last_import_batch_id=purchase_batch.import_batch_id,
                myob_item_number=item.item_number,
                supplier_name_snapshot=supplier.display_name,
                transaction_date=transaction_date,
                quantity=quantity,
                unit_price=Decimal("2"),
                line_total=quantity * Decimal("2"),
                purchase_status="B",
                currency_code="AUD",
                is_active=True,
            )
        )

    sales_batch, sales_rows = _batch(
        session,
        source_type="sales_transactions",
        source_file_name="ITEMSALES.TXT",
        marker="sales",
        count=12,
    )
    sales_dates = (
        date(2025, 7, 10),
        date(2025, 8, 10),
        date(2025, 9, 10),
        date(2025, 10, 10),
        date(2025, 11, 10),
        date(2025, 12, 10),
        date(2026, 1, 10),
        date(2026, 2, 10),
        date(2026, 3, 10),
        date(2026, 4, 10),
        date(2026, 5, 10),
        date(2026, 6, 10),
    )
    for index, transaction_date in enumerate(sales_dates, start=1):
        document = SalesDocument(
            customer_account_id=customer.customer_account_id,
            myob_customer_record_id=customer.myob_record_id,
            invoice_no=f"INV-{index}",
            first_transaction_date=transaction_date,
            last_transaction_date=transaction_date,
            line_count=1,
            first_import_batch_id=sales_batch.import_batch_id,
            last_import_batch_id=sales_batch.import_batch_id,
        )
        session.add(document)
        session.flush()
        session.add(
            SalesLine(
                sales_document_id=document.sales_document_id,
                item_id=item.item_id,
                line_sequence=1,
                source_import_row_id=sales_rows[index - 1].import_row_id,
                source_row_sha256=sales_rows[index - 1].row_sha256,
                last_import_batch_id=sales_batch.import_batch_id,
                myob_item_number=item.item_number,
                customer_name_snapshot=customer.display_name,
                transaction_date=transaction_date,
                quantity=Decimal("10"),
                unit_price=Decimal("4"),
                line_total=Decimal("40"),
                sale_status="I",
                is_active=True,
            )
        )

    snapshot = InventorySnapshot(
        captured_at=datetime(2026, 7, 1),
        source_file_name="inventory.xlsx",
        source_sha256="i" * 64,
        row_count=1,
        is_current=True,
        committed_by_user_id=actor.user_id,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        InventorySnapshotLine(
            inventory_snapshot_id=snapshot.inventory_snapshot_id,
            item_id=item.item_id,
            source_row_number=1,
            item_number_snapshot=item.item_number,
            item_name_snapshot=item.item_name,
            on_hand=Decimal("20"),
            committed=Decimal("0"),
            on_order=Decimal("30"),
            available=Decimal("20"),
        )
    )
    session.commit()
    return supplier, item


def test_supplier_waves_group_nearby_bill_dates():
    with Session(_engine()) as session:
        supplier, _ = _seed(session)
        behavior = get_supplier_receiving_behavior(
            session,
            supplier.supplier_id,
            as_of_date=date(2026, 7, 15),
        )

    assert behavior.wave_count == 3
    assert behavior.recent_waves[-1].document_count == 2
    assert behavior.median_interval_days in {182, 183}
    assert behavior.consistency in {"Regular", "Moderately regular"}
    assert behavior.next_observed_cycle_date is not None


def test_item_behavior_combines_bill_sales_supplier_and_inventory_patterns():
    with Session(_engine()) as session:
        _, item = _seed(session)
        behavior = get_item_replenishment_behavior(
            session,
            item.item_id,
            as_of_date=date(2026, 7, 15),
            demand_months=12,
            fallback_lead_days=98,
        )

    assert behavior.purchase_supplier_name == "Yuchang Textile Factory"
    assert behavior.purchase_event_count == 3
    assert behavior.typical_purchase_quantity == Decimal("120")
    assert behavior.sales_event_count == 12
    assert behavior.average_monthly_sales == Decimal("10")
    assert behavior.demand_pattern == "Regular"
    assert behavior.observed_batch_cover_months == Decimal("12")
    assert behavior.projected_pool == Decimal("50")
    assert behavior.observed_coverage_days >= 182
    assert behavior.behavioural_requirement > Decimal("50")
    assert behavior.behavioural_gap > 0
    assert behavior.confidence == "High"
