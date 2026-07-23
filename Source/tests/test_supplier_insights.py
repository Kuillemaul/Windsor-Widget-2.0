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
from windsor_widget.services.supplier_bill_links import (
    sync_supplier_links_from_bills,
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


def _add_purchase_line(
    session: Session,
    *,
    supplier: Supplier,
    item: Item,
    source_file_name: str,
    purchase_status: str,
    purchase_no: str,
    transaction_date: date,
    quantity: Decimal,
    unit_price: Decimal,
    line_sequence: int = 1,
) -> PurchaseLine:
    batch = ImportBatch(
        source_type="purchase_transactions",
        source_file_name=source_file_name,
        file_sha256=(purchase_no.lower().replace(" ", "") + "x" * 64)[:64],
        status="committed",
        row_count=1,
        accepted_row_count=1,
        committed_at=datetime.combine(transaction_date, datetime.min.time()),
    )
    session.add(batch)
    session.flush()

    source_row = ImportRow(
        import_batch_id=batch.import_batch_id,
        row_number=1,
        raw_text="row",
        raw_json='{"values": {}}',
        natural_key=f"{purchase_no}-{line_sequence}",
        row_sha256=(f"{purchase_no}-{line_sequence}" + "0" * 64)[:64],
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

    line = PurchaseLine(
        purchase_document_id=document.purchase_document_id,
        item_id=item.item_id,
        line_sequence=line_sequence,
        source_import_row_id=source_row.import_row_id,
        source_row_sha256=source_row.row_sha256,
        last_import_batch_id=batch.import_batch_id,
        myob_item_number=item.item_number,
        supplier_name_snapshot=supplier.display_name,
        transaction_date=transaction_date,
        quantity=quantity,
        unit_price=unit_price,
        line_total=quantity * unit_price,
        purchase_status=purchase_status,
        currency_code="AUD",
        order_quantity=quantity if purchase_status == "O" else None,
        received_quantity=Decimal("0") if purchase_status == "O" else quantity,
        billed_quantity=quantity if purchase_status == "B" else Decimal("0"),
        is_active=True,
    )
    session.add(line)
    return line


def _add_inventory(
    session: Session,
    *,
    actor: AppUser,
    item: Item,
) -> None:
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


def test_supplier_dashboard_counts_only_bills_from_itempurbills():
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
        _add_purchase_line(
            session,
            supplier=supplier,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="BILL100",
            transaction_date=date(2026, 5, 10),
            quantity=Decimal("20"),
            unit_price=Decimal("4.25"),
        )
        _add_purchase_line(
            session,
            supplier=supplier,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="BILL200",
            transaction_date=date(2026, 7, 2),
            quantity=Decimal("30"),
            unit_price=Decimal("4.50"),
        )
        _add_purchase_line(
            session,
            supplier=supplier,
            item=item,
            source_file_name="ITEMPUR.TXT",
            purchase_status="O",
            purchase_no="PO999",
            transaction_date=date(2026, 7, 20),
            quantity=Decimal("999"),
            unit_price=Decimal("99.99"),
        )
        _add_inventory(session, actor=actor, item=item)
        session.commit()

        dashboard = get_supplier_dashboard(
            session,
            supplier.supplier_id,
            months=12,
            as_of_date=date(2026, 7, 31),
        )
        register = list_suppliers(session)

    assert dashboard.purchase_all_time.transaction_quantity == Decimal("50")
    assert dashboard.purchase_all_time.transaction_value == Decimal("220.00")
    assert dashboard.purchase_all_time.open_quantity == Decimal("0")
    assert dashboard.default_total_lead_days == 77
    assert len(dashboard.items) == 1
    row = dashboard.items[0]
    assert row.on_hand == Decimal("50")
    assert row.inventory_on_order == Decimal("20")
    assert row.all_time_quantity == Decimal("50")
    assert row.open_quantity == Decimal("0")
    assert row.last_purchase_price == Decimal("4.50")
    assert len(dashboard.documents) == 2
    assert register[0].purchase_quantity == Decimal("50")
    assert register[0].open_quantity == Decimal("0")


def test_bill_history_creates_all_links_and_latest_supplier_is_preferred():
    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        first = Supplier(
            myob_record_id="SUP1",
            display_name="Supplier One",
            normalized_name="supplier one",
            is_active=True,
        )
        latest = Supplier(
            myob_record_id="SUP2",
            display_name="Supplier Two",
            normalized_name="supplier two",
            is_active=True,
        )
        item = Item(
            item_number="I1",
            item_name="Item One",
            normalized_name="item one",
            is_active=True,
        )
        session.add_all([actor, first, latest, item])
        session.flush()
        first_supplier_id = first.supplier_id
        latest_supplier_id = latest.supplier_id
        _add_purchase_line(
            session,
            supplier=first,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="BILL100",
            transaction_date=date(2026, 5, 1),
            quantity=Decimal("10"),
            unit_price=Decimal("4"),
        )
        _add_purchase_line(
            session,
            supplier=latest,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="BILL200",
            transaction_date=date(2026, 7, 1),
            quantity=Decimal("15"),
            unit_price=Decimal("5"),
        )
        _add_purchase_line(
            session,
            supplier=first,
            item=item,
            source_file_name="ITEMPUR.TXT",
            purchase_status="O",
            purchase_no="PO300",
            transaction_date=date(2026, 7, 20),
            quantity=Decimal("100"),
            unit_price=Decimal("99"),
        )
        session.commit()

        preview = sync_supplier_links_from_bills(session, commit=False)
        assert preview.item_supplier_pairs == 2
        assert preview.links_created == 2

        summary = sync_supplier_links_from_bills(
            session,
            commit=True,
            actor=actor,
        )
        session.commit()
        links = tuple(
            session.scalars(
                select(ItemSupplier)
                .where(ItemSupplier.item_id == item.item_id)
                .order_by(ItemSupplier.supplier_id)
            )
        )

    assert summary.links_created == 2
    assert len(links) == 2
    preferred = [link for link in links if link.is_preferred]
    assert len(preferred) == 1
    assert preferred[0].supplier_id == latest_supplier_id
    first_link = next(link for link in links if link.supplier_id == first_supplier_id)
    latest_link = next(link for link in links if link.supplier_id == latest_supplier_id)
    assert first_link.last_purchase_price == Decimal("4")
    assert latest_link.last_purchase_price == Decimal("5")
    assert latest_link.last_purchase_date == date(2026, 7, 1)


def test_bill_history_preserves_manual_preferred_and_ignores_stock():
    with Session(_engine()) as session:
        actor = AppUser(username="brad", display_name="Brad")
        manual_supplier = Supplier(
            myob_record_id="SUP1",
            display_name="Manual Supplier",
            normalized_name="manual supplier",
            is_active=True,
        )
        latest_supplier = Supplier(
            myob_record_id="SUP2",
            display_name="Latest Supplier",
            normalized_name="latest supplier",
            is_active=True,
        )
        stock = Supplier(
            myob_record_id="1462",
            display_name="STOCK",
            normalized_name="stock",
            is_active=True,
        )
        item = Item(
            item_number="I1",
            item_name="Item One",
            normalized_name="item one",
            is_active=True,
        )
        session.add_all([actor, manual_supplier, latest_supplier, stock, item])
        session.flush()
        manual_supplier_id = manual_supplier.supplier_id
        stock_supplier_id = stock.supplier_id
        session.add(
            ItemSupplier(
                item_id=item.item_id,
                supplier_id=manual_supplier_id,
                match_status="approved",
                match_method="user",
                is_preferred=True,
            )
        )
        _add_purchase_line(
            session,
            supplier=manual_supplier,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="BILL100",
            transaction_date=date(2026, 5, 1),
            quantity=Decimal("10"),
            unit_price=Decimal("4"),
        )
        _add_purchase_line(
            session,
            supplier=latest_supplier,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="BILL200",
            transaction_date=date(2026, 7, 1),
            quantity=Decimal("15"),
            unit_price=Decimal("5"),
        )
        _add_purchase_line(
            session,
            supplier=stock,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="STOCK",
            transaction_date=date(2026, 7, 20),
            quantity=Decimal("100"),
            unit_price=Decimal("99"),
        )
        session.commit()

        summary = sync_supplier_links_from_bills(
            session,
            commit=True,
            actor=actor,
        )
        session.commit()
        links = tuple(
            session.scalars(
                select(ItemSupplier).where(ItemSupplier.item_id == item.item_id)
            )
        )

    assert summary.manual_preferred_preserved == 1
    assert all(link.supplier_id != stock_supplier_id for link in links)
    preferred = [link for link in links if link.is_preferred]
    assert len(preferred) == 1
    assert preferred[0].supplier_id == manual_supplier_id


def test_supplier_edit_services_are_audited_and_use_bill_cost():
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
        _add_purchase_line(
            session,
            supplier=supplier,
            item=item,
            source_file_name="ITEMPURbills.TXT",
            purchase_status="B",
            purchase_no="BILL1",
            transaction_date=date(2026, 7, 1),
            quantity=Decimal("10"),
            unit_price=Decimal("5"),
        )
        _add_purchase_line(
            session,
            supplier=supplier,
            item=item,
            source_file_name="ITEMPUR.TXT",
            purchase_status="O",
            purchase_no="PO2",
            transaction_date=date(2026, 7, 20),
            quantity=Decimal("50"),
            unit_price=Decimal("99"),
        )
        session.commit()

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
        assert link.last_purchase_price == Decimal("5")
        assert link.last_purchase_date == date(2026, 7, 1)
        assert session.scalar(select(func.count(AuditEvent.audit_event_id))) == 2
