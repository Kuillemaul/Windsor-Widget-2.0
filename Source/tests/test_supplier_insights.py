from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    ImportBatch,
    ImportRow,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    ItemSupplier,
    PurchaseDocument,
    PurchaseLine,
    Supplier,
)
from windsor_widget.services.supplier_insights import (
    get_supplier_dashboard,
    list_suppliers,
    set_supplier_default_lead_times,
    set_supplier_item_settings,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _add_purchase(
    session: Session,
    *,
    supplier: Supplier,
    item: Item,
    actor: AppUser,
) -> None:
    batch = ImportBatch(
        source_type="purchase_transactions",
        source_file_name="purchases.TXT",
        file_sha256="p" * 64,
        status="committed",
        row_count=2,
        accepted_row_count=2,
        committed_at=datetime(2026, 7, 1),
    )
    session.add(batch)
    session.flush()

    for index, (purchase_no, transaction_date, quantity, ordered, received, price) in enumerate(
        (
            ("PO100", date(2026, 5, 10), Decimal("20"), Decimal("20"), Decimal("20"), Decimal("4.25")),
            ("PO200", date(2026, 7, 2), Decimal("30"), Decimal("30"), Decimal("10"), Decimal("4.50")),
        ),
        start=1,
    ):
        source_row = ImportRow(
            import_batch_id=batch.import_batch_id,
            row_number=index,
            raw_text="row",
            raw_json='{"values": {}}',
            natural_key=f"purchase-{index}",
            row_sha256=f"purchase-{index}".ljust(64, "0")[:64],
            status="committed",
            issue_count=0,
        )
        session.add(source_row)
        session.flush()
        document = PurchaseDocument(
            supplier_id=supplier.supplier_id,
            myob_supplier_record_id=supplier.myob_record_id,
            purchase_no=purchase_no,
            first_transaction_date=transaction_date,
            last_transaction_date=transaction_date,
            line_count=1,
            first_import_batch_id=batch.import_batch_id,
            last_import_batch_id=batch.import_batch_id,
        )
        session.add(document)
        session.flush()
        session.add(
            PurchaseLine(
                purchase_document_id=document.purchase_document_id,
                item_id=item.item_id,
                line_sequence=1,
                source_import_row_id=source_row.import_row_id,
                source_row_sha256=source_row.row_sha256,
                last_import_batch_id=batch.import_batch_id,
                myob_item_number=item.item_number,
                supplier_name_snapshot=supplier.display_name,
                transaction_date=transaction_date,
                quantity=quantity,
                unit_price=price,
                line_total=quantity * price,
                purchase_status="O" if purchase_no == "PO200" else "B",
                currency_code="AUD",
                order_quantity=ordered,
                received_quantity=received,
                billed_quantity=received,
                is_active=True,
            )
        )

    snapshot = InventorySnapshot(
        captured_at=datetime(2026, 7, 15),
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
            on_hand=Decimal("50"),
            committed=Decimal("5"),
            on_order=Decimal("20"),
            available=Decimal("45"),
        )
    )


def test_supplier_dashboard_combines_purchase_inventory_and_link_data():
    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        supplier = Supplier(
            myob_record_id="SUP1",
            display_name="Yuchang",
            normalized_name="yuchang",
            default_manufacturing_lead_days=42,
            default_transit_lead_days=28,
            default_buffer_days=7,
            is_active=True,
        )
        item = Item(
            item_number="I1",
            item_name="Item One",
            normalized_name="item one",
            is_bought=True,
            is_inventoried=True,
            is_active=True,
        )
        session.add_all([actor, supplier, item])
        session.flush()
        session.add(
            ItemSupplier(
                item_id=item.item_id,
                supplier_id=supplier.supplier_id,
                is_preferred=True,
                minimum_order_quantity=Decimal("100"),
                match_status="approved",
                match_method="user",
            )
        )
        _add_purchase(session, supplier=supplier, item=item, actor=actor)
        session.commit()

        dashboard = get_supplier_dashboard(
            session,
            supplier.supplier_id,
            months=12,
            as_of_date=date(2026, 7, 31),
        )
        register = list_suppliers(session)

    assert dashboard.display_name == "Yuchang"
    assert dashboard.purchase_all_time.transaction_quantity == Decimal("50")
    assert dashboard.purchase_all_time.received_quantity == Decimal("30")
    assert dashboard.purchase_all_time.open_quantity == Decimal("20")
    assert dashboard.default_total_lead_days == 77
    assert len(dashboard.items) == 1
    row = dashboard.items[0]
    assert row.is_preferred is True
    assert row.on_hand == Decimal("50")
    assert row.inventory_on_order == Decimal("20")
    assert row.open_quantity == Decimal("20")
    assert row.last_purchase_price == Decimal("4.50")
    assert register[0].linked_item_count == 1
    assert register[0].open_quantity == Decimal("20")


def test_supplier_edit_services_are_audited():
    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        supplier = Supplier(
            myob_record_id="SUP1",
            display_name="Yuchang",
            normalized_name="yuchang",
            is_active=True,
        )
        item = Item(
            item_number="I1",
            item_name="Item One",
            normalized_name="item one",
            is_active=True,
        )
        session.add_all([actor, supplier, item])
        session.flush()

        set_supplier_default_lead_times(
            session,
            supplier_id=supplier.supplier_id,
            manufacturing_lead_days="40",
            transit_lead_days="25",
            buffer_days="5",
            actor_user_id=actor.user_id,
        )
        link = set_supplier_item_settings(
            session,
            supplier_id=supplier.supplier_id,
            item_id=item.item_id,
            is_linked=True,
            is_preferred=True,
            supplier_item_number="YU-I1",
            minimum_order_quantity="500",
            manufacturing_lead_days_override="35",
            transit_lead_days_override="",
            buffer_days_override="10",
            actor_user_id=actor.user_id,
        )
        session.commit()

        assert supplier.default_manufacturing_lead_days == 40
        assert link is not None
        assert link.is_preferred is True
        assert link.minimum_order_quantity == Decimal("500")
        assert link.manufacturing_lead_days_override == 35
        assert link.transit_lead_days_override is None
        assert session.scalar(select(func.count(AuditEvent.audit_event_id))) == 2
