"""Manufacture-order workflow and the hand-off into the Bring In planning queue."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session, selectinload

from windsor_widget.db.models import (
    AppUser,
    AuditEvent,
    BringInRequest,
    CustomerAccount,
    Item,
    ItemSupplier,
    ManufactureLineAllocation,
    ManufactureOrder,
    ManufactureOrderLine,
    Supplier,
)
from windsor_widget.db.models.audit import utc_now

ZERO = Decimal("0")
_ORDER_STATUSES = frozenset(
    {"draft", "sent", "in_production", "ready", "closed", "cancelled"}
)
_READINESS_OVERRIDES = frozenset(
    {"auto", "delayed", "partially_ready", "confirmed_ready", "cancelled"}
)
_ALLOCATION_TYPES = frozenset({"general_stock", "customer_cover", "mto"})
_OPEN_ORDER_STATUSES = frozenset({"draft", "sent", "in_production", "ready"})


class ConcurrentOrderChange(RuntimeError):
    """Raised when another user changed an order after the page was loaded."""


@dataclass(frozen=True, slots=True)
class SelectOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class AllocationView:
    allocation_id: uuid.UUID
    allocation_type: str
    allocation_label: str
    quantity: Decimal
    customer_name: str | None
    customer_reference: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class ManufactureLineView:
    line_id: uuid.UUID
    line_sequence: int
    item_id: uuid.UUID
    item_number: str
    item_name: str
    ordered_quantity: Decimal
    cancelled_quantity: Decimal
    remaining_quantity: Decimal
    expected_ready_date: date | None
    readiness_code: str
    readiness_label: str
    readiness_tone: str
    supplier_ready_quantity: Decimal | None
    supplier_status_note: str | None
    unit_cost: Decimal | None
    currency_code: str | None
    allocation_total: Decimal
    unallocated_quantity: Decimal
    allocations: tuple[AllocationView, ...]
    active_bring_in_quantity: Decimal


@dataclass(frozen=True, slots=True)
class ManufactureOrderRow:
    order_id: uuid.UUID
    order_number: str
    supplier_id: uuid.UUID
    supplier_name: str
    order_date: date
    status: str
    status_label: str
    expected_ready_date: date | None
    line_count: int
    ordered_quantity: Decimal
    remaining_quantity: Decimal
    active_bring_in_quantity: Decimal
    version: int


@dataclass(frozen=True, slots=True)
class ManufactureOrderDetail:
    order_id: uuid.UUID
    order_number: str
    supplier_id: uuid.UUID
    supplier_name: str
    order_date: date
    status: str
    status_label: str
    expected_ready_date: date | None
    supplier_reference: str | None
    notes: str | None
    version: int
    created_by: str
    updated_by: str
    created_at: object
    updated_at: object
    source_purchase_number: str | None
    lines: tuple[ManufactureLineView, ...]
    total_ordered: Decimal
    total_remaining: Decimal
    total_bring_in: Decimal


@dataclass(frozen=True, slots=True)
class BringInRow:
    request_id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    item_id: uuid.UUID
    item_number: str
    item_name: str
    requested_quantity: Decimal
    available_open_quantity: Decimal
    priority: str
    status: str
    target_shipment_date: date | None
    reason: str | None
    source_order_id: uuid.UUID | None
    source_order_number: str | None
    created_by: str
    created_at: object


def _decimal(value: object, *, field: str, allow_zero: bool = False) -> Decimal:
    try:
        resolved = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be a valid quantity.") from exc
    if resolved < ZERO or (not allow_zero and resolved == ZERO):
        qualifier = "zero or greater" if allow_zero else "greater than zero"
        raise ValueError(f"{field} must be {qualifier}.")
    return resolved


def _optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    return _decimal(value, field=field, allow_zero=True)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _status_label(value: str) -> str:
    return {
        "draft": "Draft",
        "sent": "Sent to supplier",
        "in_production": "In production",
        "ready": "Ready",
        "closed": "Closed",
        "cancelled": "Cancelled",
    }.get(value, value.replace("_", " ").title())


def _allocation_label(value: str) -> str:
    return {
        "general_stock": "General stock",
        "customer_cover": "Customer cover",
        "mto": "Made to Order",
    }.get(value, value.replace("_", " ").title())


def _audit(
    session: Session,
    *,
    actor_user_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    summary: str,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            source="web",
            summary=summary[:500],
            before_json=json.dumps(before, default=str, sort_keys=True) if before else None,
            after_json=json.dumps(after, default=str, sort_keys=True) if after else None,
        )
    )


def _actor(session: Session, actor_user_id: uuid.UUID) -> AppUser:
    actor = session.get(AppUser, actor_user_id)
    if actor is None or not actor.is_active:
        raise LookupError("The signed-in user is no longer active.")
    return actor


def effective_manufacturing_lead_days(
    session: Session,
    *,
    supplier_id: uuid.UUID,
    item_id: uuid.UUID | None = None,
) -> int | None:
    if item_id is not None:
        override = session.scalar(
            select(ItemSupplier.manufacturing_lead_days_override).where(
                ItemSupplier.supplier_id == supplier_id,
                ItemSupplier.item_id == item_id,
                ItemSupplier.match_status != "rejected",
            )
        )
        if override is not None:
            return int(override)

    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise LookupError("Supplier not found.")
    return (
        int(supplier.default_manufacturing_lead_days)
        if supplier.default_manufacturing_lead_days is not None
        else None
    )


def expected_ready_date(
    session: Session,
    *,
    supplier_id: uuid.UUID,
    order_date: date,
    item_id: uuid.UUID | None = None,
) -> date | None:
    days = effective_manufacturing_lead_days(
        session, supplier_id=supplier_id, item_id=item_id
    )
    return order_date + timedelta(days=days) if days is not None else None


def _line_readiness(
    line: ManufactureOrderLine,
    order: ManufactureOrder,
    *,
    as_of_date: date,
) -> tuple[str, str, str]:
    remaining = Decimal(line.ordered_quantity) - Decimal(line.cancelled_quantity)
    if order.status == "cancelled" or line.readiness_override == "cancelled" or remaining <= ZERO:
        return "cancelled", "Cancelled", "muted"
    if line.readiness_override == "delayed":
        return "delayed", "Supplier reported delayed", "red"
    if line.readiness_override == "confirmed_ready":
        return "confirmed_ready", "Supplier confirmed ready", "green"
    if line.readiness_override == "partially_ready":
        quantity = line.supplier_ready_quantity or ZERO
        return "partially_ready", f"Partially ready ({quantity:,.2f})", "amber"
    if line.expected_ready_date is not None and line.expected_ready_date <= as_of_date:
        return "assumed_ready", "Assumed ready", "green"
    return "in_production", "In production", "blue"


def list_supplier_options(session: Session) -> tuple[SelectOption, ...]:
    return tuple(
        SelectOption(str(supplier_id), display_name)
        for supplier_id, display_name in session.execute(
            select(Supplier.supplier_id, Supplier.display_name)
            .where(Supplier.is_active == true())
            .order_by(Supplier.display_name)
        )
    )


def list_item_options(session: Session) -> tuple[SelectOption, ...]:
    return tuple(
        SelectOption(str(item_id), f"{item_number} — {item_name}")
        for item_id, item_number, item_name in session.execute(
            select(Item.item_id, Item.item_number, Item.item_name)
            .where(
                Item.is_active == true(),
                Item.is_bought == true(),
                Item.excluded_from_item_view != true(),
            )
            .order_by(Item.item_number)
        )
    )


def list_customer_options(session: Session) -> tuple[SelectOption, ...]:
    return tuple(
        SelectOption(str(customer_id), display_name)
        for customer_id, display_name in session.execute(
            select(CustomerAccount.customer_account_id, CustomerAccount.display_name)
            .where(CustomerAccount.is_active == true())
            .order_by(CustomerAccount.display_name)
        )
    )


def _order_statement():
    return select(ManufactureOrder).options(
        selectinload(ManufactureOrder.lines)
        .selectinload(ManufactureOrderLine.allocations)
        .selectinload(ManufactureLineAllocation.customer),
        selectinload(ManufactureOrder.lines).selectinload(
            ManufactureOrderLine.bring_in_requests
        ),
    )


def _line_view(
    line: ManufactureOrderLine,
    order: ManufactureOrder,
    *,
    as_of_date: date,
) -> ManufactureLineView:
    ordered = Decimal(line.ordered_quantity)
    cancelled = Decimal(line.cancelled_quantity)
    remaining = ordered - cancelled
    allocations = tuple(
        AllocationView(
            allocation_id=value.manufacture_line_allocation_id,
            allocation_type=value.allocation_type,
            allocation_label=_allocation_label(value.allocation_type),
            quantity=Decimal(value.quantity),
            customer_name=value.customer.display_name if value.customer else None,
            customer_reference=value.customer_reference,
            notes=value.notes,
        )
        for value in sorted(
            line.allocations,
            key=lambda allocation: (
                allocation.allocation_type,
                allocation.customer.display_name if allocation.customer else "",
            ),
        )
    )
    allocation_total = sum((value.quantity for value in allocations), ZERO)
    active_bring_in = sum(
        (
            Decimal(request.requested_quantity)
            for request in line.bring_in_requests
            if request.status == "active"
        ),
        ZERO,
    )
    readiness_code, readiness_label, readiness_tone = _line_readiness(
        line, order, as_of_date=as_of_date
    )
    return ManufactureLineView(
        line_id=line.manufacture_order_line_id,
        line_sequence=line.line_sequence,
        item_id=line.item_id,
        item_number=line.item.item_number,
        item_name=line.item.item_name,
        ordered_quantity=ordered,
        cancelled_quantity=cancelled,
        remaining_quantity=remaining,
        expected_ready_date=line.expected_ready_date,
        readiness_code=readiness_code,
        readiness_label=readiness_label,
        readiness_tone=readiness_tone,
        supplier_ready_quantity=(
            Decimal(line.supplier_ready_quantity)
            if line.supplier_ready_quantity is not None
            else None
        ),
        supplier_status_note=line.supplier_status_note,
        unit_cost=Decimal(line.unit_cost) if line.unit_cost is not None else None,
        currency_code=line.currency_code,
        allocation_total=allocation_total,
        unallocated_quantity=max(remaining - allocation_total, ZERO),
        allocations=allocations,
        active_bring_in_quantity=active_bring_in,
    )


def list_manufacture_orders(
    session: Session,
    *,
    query: str = "",
    supplier_id: uuid.UUID | None = None,
    status: str = "open",
    limit: int = 1000,
    as_of_date: date | None = None,
) -> tuple[ManufactureOrderRow, ...]:
    if status not in {"open", "all", *_ORDER_STATUSES}:
        raise ValueError("Unsupported manufacture-order status filter.")

    statement = _order_statement().order_by(
        ManufactureOrder.order_date.desc(), ManufactureOrder.order_number.desc()
    )
    if status == "open":
        statement = statement.where(ManufactureOrder.status.in_(_OPEN_ORDER_STATUSES))
    elif status != "all":
        statement = statement.where(ManufactureOrder.status == status)
    if supplier_id is not None:
        statement = statement.where(ManufactureOrder.supplier_id == supplier_id)
    if query.strip():
        pattern = f"%{query.strip().casefold()}%"
        statement = statement.join(Supplier).where(
            or_(
                func.lower(ManufactureOrder.order_number).like(pattern),
                func.lower(Supplier.display_name).like(pattern),
                func.lower(func.coalesce(ManufactureOrder.supplier_reference, "")).like(
                    pattern
                ),
            )
        )

    orders = session.scalars(statement.limit(limit)).unique().all()
    today = as_of_date or date.today()
    rows: list[ManufactureOrderRow] = []
    for order in orders:
        line_views = tuple(_line_view(line, order, as_of_date=today) for line in order.lines)
        rows.append(
            ManufactureOrderRow(
                order_id=order.manufacture_order_id,
                order_number=order.order_number,
                supplier_id=order.supplier_id,
                supplier_name=order.supplier.display_name,
                order_date=order.order_date,
                status=order.status,
                status_label=_status_label(order.status),
                expected_ready_date=order.expected_ready_date,
                line_count=len(line_views),
                ordered_quantity=sum((line.ordered_quantity for line in line_views), ZERO),
                remaining_quantity=sum((line.remaining_quantity for line in line_views), ZERO),
                active_bring_in_quantity=sum(
                    (line.active_bring_in_quantity for line in line_views), ZERO
                ),
                version=order.version,
            )
        )
    return tuple(rows)


def get_manufacture_order(
    session: Session,
    order_id: uuid.UUID,
    *,
    as_of_date: date | None = None,
) -> ManufactureOrderDetail:
    order = session.scalar(
        _order_statement().where(ManufactureOrder.manufacture_order_id == order_id)
    )
    if order is None:
        raise LookupError("Manufacture order not found.")
    today = as_of_date or date.today()
    lines = tuple(_line_view(line, order, as_of_date=today) for line in order.lines)
    return ManufactureOrderDetail(
        order_id=order.manufacture_order_id,
        order_number=order.order_number,
        supplier_id=order.supplier_id,
        supplier_name=order.supplier.display_name,
        order_date=order.order_date,
        status=order.status,
        status_label=_status_label(order.status),
        expected_ready_date=order.expected_ready_date,
        supplier_reference=order.supplier_reference,
        notes=order.notes,
        version=order.version,
        created_by=order.created_by.display_name,
        updated_by=order.updated_by.display_name,
        created_at=order.created_at,
        updated_at=order.updated_at,
        source_purchase_number=(
            order.source_purchase_document.purchase_no
            if order.source_purchase_document is not None
            else None
        ),
        lines=lines,
        total_ordered=sum((line.ordered_quantity for line in lines), ZERO),
        total_remaining=sum((line.remaining_quantity for line in lines), ZERO),
        total_bring_in=sum((line.active_bring_in_quantity for line in lines), ZERO),
    )


def create_manufacture_order(
    session: Session,
    *,
    supplier_id: uuid.UUID,
    order_number: str,
    order_date: date,
    expected_ready: date | None,
    supplier_reference: str,
    notes: str,
    actor_user_id: uuid.UUID,
) -> ManufactureOrder:
    actor = _actor(session, actor_user_id)
    supplier = session.get(Supplier, supplier_id)
    if supplier is None or not supplier.is_active:
        raise LookupError("Active supplier not found.")
    normalized_number = order_number.strip()
    if not normalized_number:
        raise ValueError("Order number is required.")
    duplicate = session.scalar(
        select(ManufactureOrder.manufacture_order_id).where(
            ManufactureOrder.supplier_id == supplier_id,
            func.lower(ManufactureOrder.order_number) == normalized_number.casefold(),
        )
    )
    if duplicate is not None:
        raise ValueError(
            f"{supplier.display_name} already has manufacture order {normalized_number}."
        )
    resolved_ready = expected_ready or expected_ready_date(
        session, supplier_id=supplier_id, order_date=order_date
    )
    order = ManufactureOrder(
        supplier_id=supplier_id,
        order_number=normalized_number,
        order_date=order_date,
        status="draft",
        expected_ready_date=resolved_ready,
        supplier_reference=_clean(supplier_reference),
        notes=_clean(notes),
        created_by_user_id=actor.user_id,
        updated_by_user_id=actor.user_id,
        version=1,
    )
    session.add(order)
    session.flush()
    _audit(
        session,
        actor_user_id=actor.user_id,
        action="manufacture_order.created",
        entity_type="manufacture_order",
        entity_id=order.manufacture_order_id,
        summary=(
            f"Created manufacture order {order.order_number} for {supplier.display_name}."
        ),
        after={
            "supplier_id": supplier_id,
            "order_number": normalized_number,
            "order_date": order_date,
            "expected_ready_date": resolved_ready,
            "status": "draft",
        },
    )
    return order


def _load_order_for_change(
    session: Session,
    order_id: uuid.UUID,
    *,
    expected_version: int,
) -> ManufactureOrder:
    order = session.get(ManufactureOrder, order_id)
    if order is None:
        raise LookupError("Manufacture order not found.")
    if order.version != expected_version:
        raise ConcurrentOrderChange(
            "This manufacture order changed after you opened it. Reload the page before saving."
        )
    return order


def _touch(order: ManufactureOrder, actor_user_id: uuid.UUID) -> None:
    order.updated_by_user_id = actor_user_id
    order.updated_at = utc_now()
    order.version += 1


def update_manufacture_order(
    session: Session,
    *,
    order_id: uuid.UUID,
    expected_version: int,
    expected_ready: date | None,
    supplier_reference: str,
    notes: str,
    actor_user_id: uuid.UUID,
) -> ManufactureOrder:
    _actor(session, actor_user_id)
    order = _load_order_for_change(
        session, order_id, expected_version=expected_version
    )
    before = {
        "expected_ready_date": order.expected_ready_date,
        "supplier_reference": order.supplier_reference,
        "notes": order.notes,
    }
    order.expected_ready_date = expected_ready
    order.supplier_reference = _clean(supplier_reference)
    order.notes = _clean(notes)
    _touch(order, actor_user_id)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="manufacture_order.updated",
        entity_type="manufacture_order",
        entity_id=order.manufacture_order_id,
        summary=f"Updated manufacture order {order.order_number}.",
        before=before,
        after={
            "expected_ready_date": order.expected_ready_date,
            "supplier_reference": order.supplier_reference,
            "notes": order.notes,
        },
    )
    session.flush()
    return order


def set_manufacture_order_status(
    session: Session,
    *,
    order_id: uuid.UUID,
    expected_version: int,
    status: str,
    actor_user_id: uuid.UUID,
) -> ManufactureOrder:
    _actor(session, actor_user_id)
    normalized = status.strip().casefold()
    if normalized not in _ORDER_STATUSES:
        raise ValueError("Unsupported manufacture-order status.")
    order = _load_order_for_change(
        session, order_id, expected_version=expected_version
    )
    old_status = order.status
    if old_status == normalized:
        return order
    order.status = normalized
    _touch(order, actor_user_id)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="manufacture_order.status_changed",
        entity_type="manufacture_order",
        entity_id=order.manufacture_order_id,
        summary=(
            f"{order.order_number} changed from {_status_label(old_status)} "
            f"to {_status_label(normalized)}."
        ),
        before={"status": old_status},
        after={"status": normalized},
    )
    session.flush()
    return order


def _resolve_item(session: Session, item_id: uuid.UUID) -> Item:
    item = session.get(Item, item_id)
    if item is None or not item.is_active or not item.is_bought:
        raise LookupError("Active purchased item not found.")
    return item


def _resolve_customer(
    session: Session,
    customer_account_id: uuid.UUID | None,
    *,
    allocation_type: str,
) -> CustomerAccount | None:
    if allocation_type == "general_stock":
        return None
    if customer_account_id is None:
        raise ValueError("Customer is required for customer cover and MTO allocations.")
    customer = session.get(CustomerAccount, customer_account_id)
    if customer is None or not customer.is_active:
        raise LookupError("Active customer not found.")
    return customer


def _new_bring_in_request(
    session: Session,
    *,
    order: ManufactureOrder,
    line: ManufactureOrderLine,
    requested_quantity: Decimal,
    actor_user_id: uuid.UUID,
) -> BringInRequest:
    request = BringInRequest(
        supplier_id=order.supplier_id,
        item_id=line.item_id,
        source_manufacture_order_line_id=line.manufacture_order_line_id,
        requested_quantity=requested_quantity,
        status="active",
        priority="manual",
        reason=(
            f"Manually added from manufacture order {order.order_number}. "
            "Stage 2 will allocate oldest suitable supplier quantities first."
        ),
        created_by_user_id=actor_user_id,
    )
    session.add(request)
    session.flush()
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="bring_in_request.created",
        entity_type="bring_in_request",
        entity_id=request.bring_in_request_id,
        summary=(
            f"Added {line.item.item_number} quantity {requested_quantity:,.2f} "
            "to the Bring In list."
        ),
        after={
            "supplier_id": order.supplier_id,
            "item_id": line.item_id,
            "requested_quantity": requested_quantity,
            "source_manufacture_order_line_id": line.manufacture_order_line_id,
        },
    )
    return request


def add_manufacture_order_line(
    session: Session,
    *,
    order_id: uuid.UUID,
    expected_version: int,
    item_id: uuid.UUID,
    ordered_quantity: object,
    expected_ready: date | None,
    unit_cost: object,
    currency_code: str,
    allocation_type: str,
    allocation_quantity: object,
    customer_account_id: uuid.UUID | None,
    customer_reference: str,
    allocation_notes: str,
    add_to_bring_in: bool,
    bring_in_quantity: object,
    actor_user_id: uuid.UUID,
) -> ManufactureOrderLine:
    _actor(session, actor_user_id)
    order = _load_order_for_change(
        session, order_id, expected_version=expected_version
    )
    if order.status in {"closed", "cancelled"}:
        raise ValueError("Lines cannot be added to a closed or cancelled order.")
    item = _resolve_item(session, item_id)
    ordered = _decimal(ordered_quantity, field="Ordered quantity")
    cost = _optional_decimal(unit_cost, field="Unit cost")
    normalized_allocation = allocation_type.strip().casefold()
    if normalized_allocation not in _ALLOCATION_TYPES:
        raise ValueError("Unsupported allocation type.")
    allocation_amount = _optional_decimal(
        allocation_quantity, field="Allocation quantity"
    )
    if allocation_amount is None:
        allocation_amount = ordered
    if allocation_amount > ordered:
        raise ValueError("Initial allocation cannot exceed the ordered quantity.")
    customer = _resolve_customer(
        session, customer_account_id, allocation_type=normalized_allocation
    )
    sequence = int(
        session.scalar(
            select(func.max(ManufactureOrderLine.line_sequence)).where(
                ManufactureOrderLine.manufacture_order_id == order.manufacture_order_id
            )
        )
        or 0
    ) + 1
    resolved_ready = expected_ready or expected_ready_date(
        session,
        supplier_id=order.supplier_id,
        item_id=item.item_id,
        order_date=order.order_date,
    ) or order.expected_ready_date
    line = ManufactureOrderLine(
        manufacture_order_id=order.manufacture_order_id,
        item_id=item.item_id,
        line_sequence=sequence,
        ordered_quantity=ordered,
        cancelled_quantity=ZERO,
        expected_ready_date=resolved_ready,
        readiness_override="auto",
        unit_cost=cost,
        currency_code=_clean(currency_code),
    )
    session.add(line)
    session.flush()
    session.add(
        ManufactureLineAllocation(
            manufacture_order_line_id=line.manufacture_order_line_id,
            allocation_type=normalized_allocation,
            customer_account_id=(
                customer.customer_account_id if customer is not None else None
            ),
            quantity=allocation_amount,
            customer_reference=_clean(customer_reference),
            notes=_clean(allocation_notes),
        )
    )
    if add_to_bring_in:
        request_quantity = _optional_decimal(
            bring_in_quantity, field="Bring In quantity"
        ) or ordered
        _new_bring_in_request(
            session,
            order=order,
            line=line,
            requested_quantity=request_quantity,
            actor_user_id=actor_user_id,
        )
    _touch(order, actor_user_id)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="manufacture_order.line_added",
        entity_type="manufacture_order_line",
        entity_id=line.manufacture_order_line_id,
        summary=(
            f"Added {item.item_number} quantity {ordered:,.2f} "
            f"to manufacture order {order.order_number}."
        ),
        after={
            "order_id": order.manufacture_order_id,
            "item_id": item.item_id,
            "ordered_quantity": ordered,
            "expected_ready_date": resolved_ready,
            "allocation_type": normalized_allocation,
            "allocation_quantity": allocation_amount,
            "customer_account_id": (
                customer.customer_account_id if customer is not None else None
            ),
            "added_to_bring_in": add_to_bring_in,
        },
    )
    session.flush()
    return line


def _line_for_order(
    session: Session,
    *,
    order: ManufactureOrder,
    line_id: uuid.UUID,
) -> ManufactureOrderLine:
    line = session.get(ManufactureOrderLine, line_id)
    if line is None or line.manufacture_order_id != order.manufacture_order_id:
        raise LookupError("Manufacture-order line not found.")
    return line


def update_manufacture_order_line(
    session: Session,
    *,
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    expected_version: int,
    ordered_quantity: object,
    cancelled_quantity: object,
    expected_ready: date | None,
    readiness_override: str,
    supplier_ready_quantity: object,
    supplier_status_note: str,
    actor_user_id: uuid.UUID,
) -> ManufactureOrderLine:
    _actor(session, actor_user_id)
    order = _load_order_for_change(
        session, order_id, expected_version=expected_version
    )
    line = _line_for_order(session, order=order, line_id=line_id)
    ordered = _decimal(ordered_quantity, field="Ordered quantity")
    cancelled = _decimal(
        cancelled_quantity, field="Cancelled quantity", allow_zero=True
    )
    if cancelled > ordered:
        raise ValueError("Cancelled quantity cannot exceed ordered quantity.")
    remaining = ordered - cancelled
    ready = _optional_decimal(
        supplier_ready_quantity, field="Supplier ready quantity"
    )
    if ready is not None and ready > remaining:
        raise ValueError("Supplier ready quantity cannot exceed the remaining quantity.")
    normalized_override = readiness_override.strip().casefold()
    if normalized_override not in _READINESS_OVERRIDES:
        raise ValueError("Unsupported readiness status.")
    allocation_total = session.scalar(
        select(func.coalesce(func.sum(ManufactureLineAllocation.quantity), 0)).where(
            ManufactureLineAllocation.manufacture_order_line_id == line_id
        )
    )
    if Decimal(allocation_total or ZERO) > remaining:
        raise ValueError(
            "Reduce or remove customer allocations before reducing this line below them."
        )
    before = {
        "ordered_quantity": line.ordered_quantity,
        "cancelled_quantity": line.cancelled_quantity,
        "expected_ready_date": line.expected_ready_date,
        "readiness_override": line.readiness_override,
        "supplier_ready_quantity": line.supplier_ready_quantity,
        "supplier_status_note": line.supplier_status_note,
    }
    line.ordered_quantity = ordered
    line.cancelled_quantity = cancelled
    line.expected_ready_date = expected_ready
    line.readiness_override = normalized_override
    line.supplier_ready_quantity = ready
    line.supplier_status_note = _clean(supplier_status_note)
    _touch(order, actor_user_id)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="manufacture_order.line_updated",
        entity_type="manufacture_order_line",
        entity_id=line.manufacture_order_line_id,
        summary=(
            f"Updated {line.item.item_number} on manufacture order {order.order_number}."
        ),
        before=before,
        after={
            "ordered_quantity": ordered,
            "cancelled_quantity": cancelled,
            "expected_ready_date": expected_ready,
            "readiness_override": normalized_override,
            "supplier_ready_quantity": ready,
            "supplier_status_note": line.supplier_status_note,
        },
    )
    session.flush()
    return line


def add_line_allocation(
    session: Session,
    *,
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    expected_version: int,
    allocation_type: str,
    quantity: object,
    customer_account_id: uuid.UUID | None,
    customer_reference: str,
    notes: str,
    actor_user_id: uuid.UUID,
) -> ManufactureLineAllocation:
    _actor(session, actor_user_id)
    order = _load_order_for_change(
        session, order_id, expected_version=expected_version
    )
    line = _line_for_order(session, order=order, line_id=line_id)
    normalized_type = allocation_type.strip().casefold()
    if normalized_type not in _ALLOCATION_TYPES:
        raise ValueError("Unsupported allocation type.")
    amount = _decimal(quantity, field="Allocation quantity")
    existing = Decimal(
        session.scalar(
            select(func.coalesce(func.sum(ManufactureLineAllocation.quantity), 0)).where(
                ManufactureLineAllocation.manufacture_order_line_id == line_id
            )
        )
        or ZERO
    )
    remaining = Decimal(line.ordered_quantity) - Decimal(line.cancelled_quantity)
    if existing + amount > remaining:
        raise ValueError("Allocations cannot exceed the line's remaining quantity.")
    customer = _resolve_customer(
        session, customer_account_id, allocation_type=normalized_type
    )
    allocation = ManufactureLineAllocation(
        manufacture_order_line_id=line_id,
        allocation_type=normalized_type,
        customer_account_id=(
            customer.customer_account_id if customer is not None else None
        ),
        quantity=amount,
        customer_reference=_clean(customer_reference),
        notes=_clean(notes),
    )
    session.add(allocation)
    session.flush()
    _touch(order, actor_user_id)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="manufacture_order.allocation_added",
        entity_type="manufacture_line_allocation",
        entity_id=allocation.manufacture_line_allocation_id,
        summary=(
            f"Allocated {amount:,.2f} of {line.item.item_number} as "
            f"{_allocation_label(normalized_type)}."
        ),
        after={
            "line_id": line_id,
            "allocation_type": normalized_type,
            "quantity": amount,
            "customer_account_id": (
                customer.customer_account_id if customer is not None else None
            ),
        },
    )
    session.flush()
    return allocation


def delete_line_allocation(
    session: Session,
    *,
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    allocation_id: uuid.UUID,
    expected_version: int,
    actor_user_id: uuid.UUID,
) -> None:
    _actor(session, actor_user_id)
    order = _load_order_for_change(
        session, order_id, expected_version=expected_version
    )
    line = _line_for_order(session, order=order, line_id=line_id)
    allocation = session.get(ManufactureLineAllocation, allocation_id)
    if allocation is None or allocation.manufacture_order_line_id != line_id:
        raise LookupError("Allocation not found.")
    summary = (
        f"Removed {Decimal(allocation.quantity):,.2f} "
        f"{_allocation_label(allocation.allocation_type)} allocation from "
        f"{line.item.item_number}."
    )
    before = {
        "allocation_type": allocation.allocation_type,
        "quantity": allocation.quantity,
        "customer_account_id": allocation.customer_account_id,
    }
    session.delete(allocation)
    _touch(order, actor_user_id)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="manufacture_order.allocation_removed",
        entity_type="manufacture_line_allocation",
        entity_id=allocation_id,
        summary=summary,
        before=before,
    )
    session.flush()


def add_existing_line_to_bring_in(
    session: Session,
    *,
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    expected_version: int,
    requested_quantity: object,
    actor_user_id: uuid.UUID,
) -> BringInRequest:
    _actor(session, actor_user_id)
    order = _load_order_for_change(
        session, order_id, expected_version=expected_version
    )
    line = _line_for_order(session, order=order, line_id=line_id)
    amount = _decimal(requested_quantity, field="Bring In quantity")
    request = _new_bring_in_request(
        session,
        order=order,
        line=line,
        requested_quantity=amount,
        actor_user_id=actor_user_id,
    )
    _touch(order, actor_user_id)
    session.flush()
    return request


def list_bring_in_requests(
    session: Session,
    *,
    status: str = "active",
    supplier_id: uuid.UUID | None = None,
    query: str = "",
    limit: int = 2000,
) -> tuple[BringInRow, ...]:
    if status not in {"all", "active", "allocated", "completed", "cancelled"}:
        raise ValueError("Unsupported Bring In status filter.")
    statement = (
        select(BringInRequest)
        .options(
            selectinload(BringInRequest.source_line).selectinload(
                ManufactureOrderLine.order
            )
        )
        .order_by(
            BringInRequest.priority.desc(),
            BringInRequest.target_shipment_date,
            BringInRequest.created_at,
        )
    )
    if status != "all":
        statement = statement.where(BringInRequest.status == status)
    if supplier_id is not None:
        statement = statement.where(BringInRequest.supplier_id == supplier_id)
    if query.strip():
        pattern = f"%{query.strip().casefold()}%"
        statement = statement.join(Item).join(Supplier).where(
            or_(
                func.lower(Item.item_number).like(pattern),
                func.lower(Item.item_name).like(pattern),
                func.lower(Supplier.display_name).like(pattern),
            )
        )
    requests = session.scalars(statement.limit(limit)).unique().all()
    available_rows = session.execute(
        select(
            ManufactureOrder.supplier_id,
            ManufactureOrderLine.item_id,
            func.coalesce(
                func.sum(
                    ManufactureOrderLine.ordered_quantity
                    - ManufactureOrderLine.cancelled_quantity
                ),
                0,
            ),
        )
        .select_from(ManufactureOrderLine)
        .join(
            ManufactureOrder,
            ManufactureOrder.manufacture_order_id
            == ManufactureOrderLine.manufacture_order_id,
        )
        .where(ManufactureOrder.status.in_(_OPEN_ORDER_STATUSES))
        .group_by(ManufactureOrder.supplier_id, ManufactureOrderLine.item_id)
    ).all()
    available = {
        (supplier_key, item_key): Decimal(quantity or ZERO)
        for supplier_key, item_key, quantity in available_rows
    }
    return tuple(
        BringInRow(
            request_id=request.bring_in_request_id,
            supplier_id=request.supplier_id,
            supplier_name=request.supplier.display_name,
            item_id=request.item_id,
            item_number=request.item.item_number,
            item_name=request.item.item_name,
            requested_quantity=Decimal(request.requested_quantity),
            available_open_quantity=available.get(
                (request.supplier_id, request.item_id), ZERO
            ),
            priority=request.priority,
            status=request.status,
            target_shipment_date=request.target_shipment_date,
            reason=request.reason,
            source_order_id=(
                request.source_line.manufacture_order_id
                if request.source_line is not None
                else None
            ),
            source_order_number=(
                request.source_line.order.order_number
                if request.source_line is not None
                else None
            ),
            created_by=request.created_by.display_name,
            created_at=request.created_at,
        )
        for request in requests
    )


def cancel_bring_in_request(
    session: Session,
    *,
    request_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> BringInRequest:
    _actor(session, actor_user_id)
    request = session.get(BringInRequest, request_id)
    if request is None:
        raise LookupError("Bring In request not found.")
    if request.status == "cancelled":
        return request
    before = {"status": request.status}
    request.status = "cancelled"
    request.cancelled_by_user_id = actor_user_id
    request.updated_at = utc_now()
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="bring_in_request.cancelled",
        entity_type="bring_in_request",
        entity_id=request.bring_in_request_id,
        summary=(
            f"Removed {request.item.item_number} quantity "
            f"{Decimal(request.requested_quantity):,.2f} from the Bring In list."
        ),
        before=before,
        after={"status": "cancelled"},
    )
    session.flush()
    return request
