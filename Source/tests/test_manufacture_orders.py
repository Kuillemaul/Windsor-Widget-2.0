from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from windsor_widget.db.base import Base
from windsor_widget.db.models import (
    AppUser,
    BringInRequest,
    CustomerAccount,
    Item,
    ItemSupplier,
    ManufactureLineAllocation,
    Supplier,
)
from windsor_widget.services.manufacture_orders import (
    ConcurrentOrderChange,
    add_line_allocation,
    add_manufacture_order_line,
    create_manufacture_order,
    effective_manufacturing_lead_days,
    get_manufacture_order,
    update_manufacture_order,
)


def build_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def seed(session: Session):
    actor = AppUser(username="brad", display_name="Brad Mayze", is_active=True)
    supplier = Supplier(
        display_name="YU",
        normalized_name="yu",
        default_manufacturing_lead_days=60,
        is_active=True,
    )
    item = Item(
        item_number="ITEM/1",
        item_name="Test Item",
        normalized_name="test item",
        is_bought=True,
        is_sold=True,
        is_inventoried=True,
        is_active=True,
        excluded_from_item_view=False,
        replenishment_policy="stocked",
        policy_source="user",
    )
    customer = CustomerAccount(
        display_name="Customer A",
        normalized_name="customer a",
        payment_basis="account",
        freight_payer="customer",
        group_match_status="unmatched",
        is_active=True,
    )
    session.add_all([actor, supplier, item, customer])
    session.flush()
    session.add(
        ItemSupplier(
            item_id=item.item_id,
            supplier_id=supplier.supplier_id,
            is_preferred=True,
            manufacturing_lead_days_override=45,
            match_status="approved",
            match_method="user",
        )
    )
    session.flush()
    return actor, supplier, item, customer


def test_item_supplier_lead_time_overrides_supplier_default():
    with build_session() as session:
        _, supplier, item, _ = seed(session)
        assert (
            effective_manufacturing_lead_days(
                session,
                supplier_id=supplier.supplier_id,
                item_id=item.item_id,
            )
            == 45
        )
        assert (
            effective_manufacturing_lead_days(
                session,
                supplier_id=supplier.supplier_id,
            )
            == 60
        )


def test_create_line_preserves_customer_allocation_and_creates_bring_in_request():
    with build_session() as session:
        actor, supplier, item, customer = seed(session)
        order = create_manufacture_order(
            session,
            supplier_id=supplier.supplier_id,
            order_number="YU-1001",
            order_date=date(2026, 7, 1),
            expected_ready=None,
            supplier_reference="",
            notes="",
            actor_user_id=actor.user_id,
        )
        assert order.expected_ready_date == date(2026, 8, 30)

        line = add_manufacture_order_line(
            session,
            order_id=order.manufacture_order_id,
            expected_version=1,
            item_id=item.item_id,
            ordered_quantity="100",
            expected_ready=None,
            unit_cost="2.50",
            currency_code="USD",
            allocation_type="customer_cover",
            allocation_quantity="60",
            customer_account_id=customer.customer_account_id,
            customer_reference="COVER-1",
            allocation_notes="Customer cover",
            add_to_bring_in=True,
            bring_in_quantity="80",
            actor_user_id=actor.user_id,
        )
        session.flush()

        assert line.expected_ready_date == date(2026, 8, 15)
        allocation = session.scalar(select(ManufactureLineAllocation))
        assert allocation is not None
        assert allocation.quantity == Decimal("60")
        assert allocation.customer_account_id == customer.customer_account_id
        request = session.scalar(select(BringInRequest))
        assert request is not None
        assert request.requested_quantity == Decimal("80")
        assert request.source_manufacture_order_line_id == line.manufacture_order_line_id

        detail = get_manufacture_order(
            session,
            order.manufacture_order_id,
            as_of_date=date(2026, 8, 16),
        )
        assert detail.lines[0].readiness_code == "assumed_ready"
        assert detail.lines[0].allocation_total == Decimal("60")
        assert detail.lines[0].unallocated_quantity == Decimal("40")
        assert detail.lines[0].active_bring_in_quantity == Decimal("80")


def test_allocations_cannot_exceed_open_line_quantity():
    with build_session() as session:
        actor, supplier, item, customer = seed(session)
        order = create_manufacture_order(
            session,
            supplier_id=supplier.supplier_id,
            order_number="YU-1002",
            order_date=date(2026, 7, 1),
            expected_ready=None,
            supplier_reference="",
            notes="",
            actor_user_id=actor.user_id,
        )
        line = add_manufacture_order_line(
            session,
            order_id=order.manufacture_order_id,
            expected_version=1,
            item_id=item.item_id,
            ordered_quantity="100",
            expected_ready=None,
            unit_cost="",
            currency_code="",
            allocation_type="general_stock",
            allocation_quantity="70",
            customer_account_id=None,
            customer_reference="",
            allocation_notes="",
            add_to_bring_in=False,
            bring_in_quantity="",
            actor_user_id=actor.user_id,
        )
        with pytest.raises(ValueError, match="cannot exceed"):
            add_line_allocation(
                session,
                order_id=order.manufacture_order_id,
                line_id=line.manufacture_order_line_id,
                expected_version=2,
                allocation_type="customer_cover",
                quantity="31",
                customer_account_id=customer.customer_account_id,
                customer_reference="",
                notes="",
                actor_user_id=actor.user_id,
            )


def test_stale_order_version_is_rejected():
    with build_session() as session:
        actor, supplier, _, _ = seed(session)
        order = create_manufacture_order(
            session,
            supplier_id=supplier.supplier_id,
            order_number="YU-1003",
            order_date=date(2026, 7, 1),
            expected_ready=None,
            supplier_reference="",
            notes="",
            actor_user_id=actor.user_id,
        )
        update_manufacture_order(
            session,
            order_id=order.manufacture_order_id,
            expected_version=1,
            expected_ready=date(2026, 9, 1),
            supplier_reference="REV-A",
            notes="",
            actor_user_id=actor.user_id,
        )
        with pytest.raises(ConcurrentOrderChange):
            update_manufacture_order(
                session,
                order_id=order.manufacture_order_id,
                expected_version=1,
                expected_ready=date(2026, 9, 2),
                supplier_reference="REV-B",
                notes="",
                actor_user_id=actor.user_id,
            )
