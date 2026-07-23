"""Supplier register, Supplier Summary and audited supplier-item settings."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    AuditEvent,
    InventorySnapshot,
    InventorySnapshotLine,
    ImportBatch,
    Item,
    ItemSupplier,
    PurchaseDocument,
    PurchaseLine,
    Supplier,
)
from windsor_widget.db.models.audit import utc_now
from windsor_widget.services.purchase_bill_rules import (
    purchase_bill_conditions,
    real_supplier_condition,
)

_ZERO = Decimal("0")
_MAX_LEAD_DAYS = 3650


@dataclass(frozen=True, slots=True)
class SupplierActivityTotals:
    document_count: int
    line_count: int
    transaction_quantity: Decimal
    transaction_value: Decimal
    ordered_quantity: Decimal
    received_quantity: Decimal
    billed_quantity: Decimal
    open_quantity: Decimal
    first_date: date | None
    last_date: date | None


@dataclass(frozen=True, slots=True)
class SupplierRegisterRow:
    supplier_id: uuid.UUID
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    card_status: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    is_active: bool
    default_manufacturing_lead_days: int | None
    default_transit_lead_days: int | None
    default_buffer_days: int | None
    linked_item_count: int
    purchase_document_count: int
    purchase_line_count: int
    purchase_quantity: Decimal
    purchase_value: Decimal
    open_quantity: Decimal
    last_purchase_date: date | None

    @property
    def default_total_lead_days(self) -> int:
        return (
            int(self.default_manufacturing_lead_days or 0)
            + int(self.default_transit_lead_days or 0)
            + int(self.default_buffer_days or 0)
        )


@dataclass(frozen=True, slots=True)
class SupplierItemRow:
    item_id: uuid.UUID
    item_supplier_id: uuid.UUID | None
    item_number: str
    item_name: str
    supplier_item_number: str | None
    match_status: str
    match_method: str | None
    is_linked: bool
    is_preferred: bool
    minimum_order_quantity: Decimal | None
    manufacturing_lead_days_override: int | None
    transit_lead_days_override: int | None
    buffer_days_override: int | None
    effective_manufacturing_lead_days: int
    effective_transit_lead_days: int
    effective_buffer_days: int
    period_quantity: Decimal
    period_value: Decimal
    all_time_quantity: Decimal
    all_time_value: Decimal
    ordered_quantity: Decimal
    received_quantity: Decimal
    billed_quantity: Decimal
    open_quantity: Decimal
    last_purchase_date: date | None
    last_purchase_price: Decimal | None
    last_purchase_currency: str | None
    on_hand: Decimal
    inventory_on_order: Decimal
    available: Decimal

    @property
    def effective_total_lead_days(self) -> int:
        return (
            self.effective_manufacturing_lead_days
            + self.effective_transit_lead_days
            + self.effective_buffer_days
        )


@dataclass(frozen=True, slots=True)
class SupplierPurchaseDocumentRow:
    purchase_document_id: uuid.UUID
    purchase_no: str
    first_transaction_date: date
    last_transaction_date: date
    line_count: int
    transaction_quantity: Decimal
    order_quantity: Decimal
    received_quantity: Decimal
    billed_quantity: Decimal
    open_quantity: Decimal
    value: Decimal
    currency_code: str | None
    status_summary: str
    latest_shipping_date: date | None
    supplier_invoice_no: str | None


@dataclass(frozen=True, slots=True)
class SupplierDashboard:
    supplier_id: uuid.UUID
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    card_status: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    is_active: bool
    default_manufacturing_lead_days: int | None
    default_transit_lead_days: int | None
    default_buffer_days: int | None
    period_start: date
    as_of_date: date
    purchase_period: SupplierActivityTotals
    purchase_all_time: SupplierActivityTotals
    items: tuple[SupplierItemRow, ...]
    documents: tuple[SupplierPurchaseDocumentRow, ...]

    @property
    def default_total_lead_days(self) -> int:
        return (
            int(self.default_manufacturing_lead_days or 0)
            + int(self.default_transit_lead_days or 0)
            + int(self.default_buffer_days or 0)
        )

    @property
    def linked_item_count(self) -> int:
        return sum(1 for item in self.items if item.is_linked)


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _period_start(as_of_date: date, months: int) -> date:
    if months < 1 or months > 120:
        raise ValueError("months must be between 1 and 120")
    month_index = as_of_date.year * 12 + as_of_date.month - 1 - (months - 1)
    year, zero_month = divmod(month_index, 12)
    return date(year, zero_month + 1, 1)


def _optional_nonnegative_int(value: int | str | None, label: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number of days.") from exc
    if parsed < 0 or parsed > _MAX_LEAD_DAYS:
        raise ValueError(f"{label} must be between 0 and {_MAX_LEAD_DAYS} days.")
    return parsed


def _optional_nonnegative_decimal(value: Decimal | str | None, label: str) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if parsed < 0:
        raise ValueError(f"{label} cannot be negative.")
    return parsed


def _activity_totals(
    session: Session,
    supplier_id: uuid.UUID,
    *,
    start_date: date | None,
    as_of_date: date,
) -> SupplierActivityTotals:
    conditions = [
        PurchaseDocument.supplier_id == supplier_id,
        PurchaseLine.transaction_date <= as_of_date,
        *purchase_bill_conditions(as_of_date=as_of_date),
    ]
    if start_date is not None:
        conditions.append(PurchaseLine.transaction_date >= start_date)

    row = session.execute(
        select(
            func.count(func.distinct(PurchaseDocument.purchase_document_id)),
            func.count(PurchaseLine.purchase_line_id),
            func.coalesce(func.sum(PurchaseLine.quantity), 0),
            func.coalesce(func.sum(PurchaseLine.line_total), 0),
            func.min(PurchaseLine.transaction_date),
            func.max(PurchaseLine.transaction_date),
        )
        .select_from(PurchaseLine)
        .join(
            PurchaseDocument,
            PurchaseDocument.purchase_document_id == PurchaseLine.purchase_document_id,
        )
        .join(
            ImportBatch,
            ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
        )
        .join(
            Supplier,
            Supplier.supplier_id == PurchaseDocument.supplier_id,
        )
        .where(*conditions, real_supplier_condition())
    ).one()

    transaction_quantity = _decimal(row[2])
    return SupplierActivityTotals(
        document_count=int(row[0] or 0),
        line_count=int(row[1] or 0),
        transaction_quantity=transaction_quantity,
        transaction_value=_decimal(row[3]),
        ordered_quantity=_ZERO,
        received_quantity=_ZERO,
        billed_quantity=transaction_quantity,
        open_quantity=_ZERO,
        first_date=row[4],
        last_date=row[5],
    )

def list_suppliers(
    session: Session,
    *,
    query: str = "",
    status: str = "active",
    limit: int = 1000,
) -> tuple[SupplierRegisterRow, ...]:
    purchase_aggregate = (
        select(
            PurchaseDocument.supplier_id.label("supplier_id"),
            func.count(func.distinct(PurchaseDocument.purchase_document_id)).label(
                "document_count"
            ),
            func.count(PurchaseLine.purchase_line_id).label("line_count"),
            func.coalesce(func.sum(PurchaseLine.quantity), 0).label("quantity"),
            func.coalesce(func.sum(PurchaseLine.line_total), 0).label("value"),
            func.max(PurchaseLine.transaction_date).label("last_purchase_date"),
        )
        .select_from(PurchaseLine)
        .join(
            PurchaseDocument,
            PurchaseDocument.purchase_document_id == PurchaseLine.purchase_document_id,
        )
        .join(
            ImportBatch,
            ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
        )
        .join(
            Supplier,
            Supplier.supplier_id == PurchaseDocument.supplier_id,
        )
        .where(
            *purchase_bill_conditions(),
            real_supplier_condition(),
        )
        .group_by(PurchaseDocument.supplier_id)
        .subquery()
    )

    linked_items = (
        select(
            ItemSupplier.supplier_id.label("supplier_id"),
            func.count(func.distinct(ItemSupplier.item_id)).label("linked_item_count"),
        )
        .where(ItemSupplier.match_status != "rejected")
        .group_by(ItemSupplier.supplier_id)
        .subquery()
    )

    statement = (
        select(
            Supplier,
            purchase_aggregate.c.document_count,
            purchase_aggregate.c.line_count,
            purchase_aggregate.c.quantity,
            purchase_aggregate.c.value,
            purchase_aggregate.c.last_purchase_date,
            linked_items.c.linked_item_count,
        )
        .outerjoin(
            purchase_aggregate,
            purchase_aggregate.c.supplier_id == Supplier.supplier_id,
        )
        .outerjoin(
            linked_items,
            linked_items.c.supplier_id == Supplier.supplier_id,
        )
        .where(real_supplier_condition())
        .order_by(Supplier.display_name)
        .limit(limit)
    )

    normalized_status = status.strip().casefold()
    if normalized_status == "active":
        statement = statement.where(Supplier.is_active == true())
    elif normalized_status == "inactive":
        statement = statement.where(Supplier.is_active != true())
    elif normalized_status not in {"", "all"}:
        raise ValueError(f"Unsupported supplier status filter: {status!r}")

    needle = query.strip()
    if needle:
        like = f"%{needle}%"
        statement = statement.where(
            or_(
                Supplier.display_name.ilike(like),
                Supplier.myob_record_id.ilike(like),
                Supplier.myob_card_id.ilike(like),
                Supplier.contact_name.ilike(like),
                Supplier.email.ilike(like),
            )
        )

    else:
        # Default register view hides supplier cards without bill history.
        # A search deliberately removes this restriction so dormant or
        # never-used supplier cards can still be found.
        statement = statement.where(
            purchase_aggregate.c.document_count.is_not(None)
        )

    rows: list[SupplierRegisterRow] = []
    for row in session.execute(statement):
        supplier = row[0]
        rows.append(
            SupplierRegisterRow(
                supplier_id=supplier.supplier_id,
                myob_record_id=supplier.myob_record_id,
                myob_card_id=supplier.myob_card_id,
                display_name=supplier.display_name,
                card_status=supplier.card_status,
                contact_name=supplier.contact_name,
                email=supplier.email,
                phone=supplier.phone,
                is_active=supplier.is_active,
                default_manufacturing_lead_days=supplier.default_manufacturing_lead_days,
                default_transit_lead_days=supplier.default_transit_lead_days,
                default_buffer_days=supplier.default_buffer_days,
                linked_item_count=int(row[6] or 0),
                purchase_document_count=int(row[1] or 0),
                purchase_line_count=int(row[2] or 0),
                purchase_quantity=_decimal(row[3]),
                purchase_value=_decimal(row[4]),
                open_quantity=_ZERO,
                last_purchase_date=row[5],
            )
        )
    return tuple(rows)

def _supplier_item_rows(
    session: Session,
    supplier: Supplier,
    *,
    period_start: date,
    as_of_date: date,
) -> tuple[SupplierItemRow, ...]:
    def purchase_aggregate(start_date: date | None):
        conditions = [
            PurchaseDocument.supplier_id == supplier.supplier_id,
            PurchaseLine.item_id.is_not(None),
            PurchaseLine.transaction_date <= as_of_date,
            *purchase_bill_conditions(as_of_date=as_of_date),
        ]
        if start_date is not None:
            conditions.append(PurchaseLine.transaction_date >= start_date)
        return (
            select(
                PurchaseLine.item_id.label("item_id"),
                func.coalesce(func.sum(PurchaseLine.quantity), 0).label("quantity"),
                func.coalesce(func.sum(PurchaseLine.line_total), 0).label("value"),
                func.max(PurchaseLine.transaction_date).label("last_purchase_date"),
            )
            .select_from(PurchaseLine)
            .join(
                PurchaseDocument,
                PurchaseDocument.purchase_document_id
                == PurchaseLine.purchase_document_id,
            )
            .join(
                ImportBatch,
                ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
            )
            .join(
                Supplier,
                Supplier.supplier_id == PurchaseDocument.supplier_id,
            )
            .where(*conditions, real_supplier_condition())
            .group_by(PurchaseLine.item_id)
            .subquery()
        )

    all_time = purchase_aggregate(None)
    period = purchase_aggregate(period_start)

    ranked_prices = (
        select(
            PurchaseLine.item_id.label("item_id"),
            PurchaseLine.unit_price.label("unit_price"),
            PurchaseLine.currency_code.label("currency_code"),
            PurchaseLine.transaction_date.label("transaction_date"),
            func.row_number()
            .over(
                partition_by=PurchaseLine.item_id,
                order_by=(
                    PurchaseLine.transaction_date.desc(),
                    PurchaseDocument.purchase_no.desc(),
                    PurchaseLine.line_sequence.desc(),
                ),
            )
            .label("price_rank"),
        )
        .select_from(PurchaseLine)
        .join(
            PurchaseDocument,
            PurchaseDocument.purchase_document_id == PurchaseLine.purchase_document_id,
        )
        .join(
            ImportBatch,
            ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
        )
        .join(
            Supplier,
            Supplier.supplier_id == PurchaseDocument.supplier_id,
        )
        .where(
            PurchaseDocument.supplier_id == supplier.supplier_id,
            PurchaseLine.item_id.is_not(None),
            *purchase_bill_conditions(
                as_of_date=as_of_date,
                positive_quantity_only=True,
            ),
            real_supplier_condition(),
        )
        .subquery()
    )
    latest_price = (
        select(
            ranked_prices.c.item_id,
            ranked_prices.c.unit_price,
            ranked_prices.c.currency_code,
            ranked_prices.c.transaction_date,
        )
        .where(ranked_prices.c.price_rank == 1)
        .subquery()
    )

    inventory = (
        select(
            InventorySnapshotLine.item_id.label("item_id"),
            InventorySnapshotLine.on_hand.label("on_hand"),
            InventorySnapshotLine.on_order.label("inventory_on_order"),
            InventorySnapshotLine.available.label("available"),
        )
        .select_from(InventorySnapshotLine)
        .join(
            InventorySnapshot,
            InventorySnapshot.inventory_snapshot_id
            == InventorySnapshotLine.inventory_snapshot_id,
        )
        .where(InventorySnapshot.is_current == true())
        .subquery()
    )

    statement = (
        select(
            Item,
            ItemSupplier,
            period.c.quantity,
            period.c.value,
            all_time.c.quantity,
            all_time.c.value,
            all_time.c.last_purchase_date,
            latest_price.c.unit_price,
            latest_price.c.currency_code,
            inventory.c.on_hand,
            inventory.c.inventory_on_order,
            inventory.c.available,
        )
        .outerjoin(
            ItemSupplier,
            and_(
                ItemSupplier.item_id == Item.item_id,
                ItemSupplier.supplier_id == supplier.supplier_id,
            ),
        )
        .outerjoin(all_time, all_time.c.item_id == Item.item_id)
        .outerjoin(period, period.c.item_id == Item.item_id)
        .outerjoin(latest_price, latest_price.c.item_id == Item.item_id)
        .outerjoin(inventory, inventory.c.item_id == Item.item_id)
        .where(
            or_(
                all_time.c.item_id.is_not(None),
                and_(
                    ItemSupplier.item_supplier_id.is_not(None),
                    ItemSupplier.match_status != "rejected",
                ),
            )
        )
        .order_by(
            func.coalesce(period.c.quantity, 0).desc(),
            func.coalesce(all_time.c.quantity, 0).desc(),
            Item.item_number,
        )
    )

    result: list[SupplierItemRow] = []
    for row in session.execute(statement):
        item = row[0]
        link = row[1]
        manufacturing = (
            link.manufacturing_lead_days_override
            if link is not None and link.manufacturing_lead_days_override is not None
            else supplier.default_manufacturing_lead_days
        )
        transit = (
            link.transit_lead_days_override
            if link is not None and link.transit_lead_days_override is not None
            else supplier.default_transit_lead_days
        )
        buffer = (
            link.buffer_days_override
            if link is not None and link.buffer_days_override is not None
            else supplier.default_buffer_days
        )
        linked = link is not None and link.match_status != "rejected"
        all_time_quantity = _decimal(row[4])
        result.append(
            SupplierItemRow(
                item_id=item.item_id,
                item_supplier_id=link.item_supplier_id if link is not None else None,
                item_number=item.item_number,
                item_name=item.item_name,
                supplier_item_number=link.supplier_item_number if link else None,
                match_status=link.match_status if link else "unlinked",
                match_method=link.match_method if link else None,
                is_linked=linked,
                is_preferred=bool(link.is_preferred) if linked else False,
                minimum_order_quantity=link.minimum_order_quantity if linked else None,
                manufacturing_lead_days_override=(
                    link.manufacturing_lead_days_override if linked else None
                ),
                transit_lead_days_override=(
                    link.transit_lead_days_override if linked else None
                ),
                buffer_days_override=link.buffer_days_override if linked else None,
                effective_manufacturing_lead_days=int(manufacturing or 0),
                effective_transit_lead_days=int(transit or 0),
                effective_buffer_days=int(buffer or 0),
                period_quantity=_decimal(row[2]),
                period_value=_decimal(row[3]),
                all_time_quantity=all_time_quantity,
                all_time_value=_decimal(row[5]),
                ordered_quantity=_ZERO,
                received_quantity=_ZERO,
                billed_quantity=all_time_quantity,
                open_quantity=_ZERO,
                last_purchase_date=row[6],
                last_purchase_price=(
                    _decimal(row[7]) if row[7] is not None else None
                ),
                last_purchase_currency=row[8],
                on_hand=_decimal(row[9]),
                inventory_on_order=_decimal(row[10]),
                available=_decimal(row[11]),
            )
        )
    return tuple(result)

def _supplier_documents(
    session: Session,
    supplier_id: uuid.UUID,
    *,
    as_of_date: date,
    limit: int = 200,
) -> tuple[SupplierPurchaseDocumentRow, ...]:
    rows = session.execute(
        select(
            PurchaseDocument.purchase_document_id,
            PurchaseDocument.purchase_no,
            PurchaseDocument.first_transaction_date,
            PurchaseDocument.last_transaction_date,
            func.count(PurchaseLine.purchase_line_id),
            func.coalesce(func.sum(PurchaseLine.quantity), 0),
            func.coalesce(func.sum(PurchaseLine.line_total), 0),
            func.min(PurchaseLine.currency_code),
            func.max(PurchaseLine.shipping_date),
            func.max(PurchaseLine.supplier_invoice_no),
        )
        .select_from(PurchaseDocument)
        .join(
            PurchaseLine,
            PurchaseLine.purchase_document_id == PurchaseDocument.purchase_document_id,
        )
        .join(
            ImportBatch,
            ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
        )
        .join(
            Supplier,
            Supplier.supplier_id == PurchaseDocument.supplier_id,
        )
        .where(
            PurchaseDocument.supplier_id == supplier_id,
            *purchase_bill_conditions(as_of_date=as_of_date),
            real_supplier_condition(),
        )
        .group_by(
            PurchaseDocument.purchase_document_id,
            PurchaseDocument.purchase_no,
            PurchaseDocument.first_transaction_date,
            PurchaseDocument.last_transaction_date,
        )
        .order_by(
            PurchaseDocument.last_transaction_date.desc(),
            PurchaseDocument.purchase_no.desc(),
        )
        .limit(limit)
    )

    result: list[SupplierPurchaseDocumentRow] = []
    for row in rows:
        transaction_quantity = _decimal(row[5])
        result.append(
            SupplierPurchaseDocumentRow(
                purchase_document_id=row[0],
                purchase_no=row[1],
                first_transaction_date=row[2],
                last_transaction_date=row[3],
                line_count=int(row[4] or 0),
                transaction_quantity=transaction_quantity,
                order_quantity=_ZERO,
                received_quantity=_ZERO,
                billed_quantity=transaction_quantity,
                open_quantity=_ZERO,
                value=_decimal(row[6]),
                currency_code=row[7],
                status_summary="Bill",
                latest_shipping_date=row[8],
                supplier_invoice_no=row[9],
            )
        )
    return tuple(result)

def get_supplier_dashboard(
    session: Session,
    supplier_id: uuid.UUID,
    *,
    months: int,
    as_of_date: date,
) -> SupplierDashboard:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise LookupError(f"No supplier exists for {supplier_id}.")

    period_start = _period_start(as_of_date, months)
    return SupplierDashboard(
        supplier_id=supplier.supplier_id,
        myob_record_id=supplier.myob_record_id,
        myob_card_id=supplier.myob_card_id,
        display_name=supplier.display_name,
        card_status=supplier.card_status,
        contact_name=supplier.contact_name,
        email=supplier.email,
        phone=supplier.phone,
        is_active=supplier.is_active,
        default_manufacturing_lead_days=supplier.default_manufacturing_lead_days,
        default_transit_lead_days=supplier.default_transit_lead_days,
        default_buffer_days=supplier.default_buffer_days,
        period_start=period_start,
        as_of_date=as_of_date,
        purchase_period=_activity_totals(
            session,
            supplier_id,
            start_date=period_start,
            as_of_date=as_of_date,
        ),
        purchase_all_time=_activity_totals(
            session,
            supplier_id,
            start_date=None,
            as_of_date=as_of_date,
        ),
        items=_supplier_item_rows(
            session,
            supplier,
            period_start=period_start,
            as_of_date=as_of_date,
        ),
        documents=_supplier_documents(
            session,
            supplier_id,
            as_of_date=as_of_date,
        ),
    )


def set_supplier_default_lead_times(
    session: Session,
    *,
    supplier_id: uuid.UUID,
    manufacturing_lead_days: int | str | None,
    transit_lead_days: int | str | None,
    buffer_days: int | str | None,
    actor_user_id: uuid.UUID,
) -> Supplier:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise LookupError("Supplier not found.")

    after = {
        "default_manufacturing_lead_days": _optional_nonnegative_int(
            manufacturing_lead_days, "Manufacturing lead time"
        ),
        "default_transit_lead_days": _optional_nonnegative_int(
            transit_lead_days, "Transit lead time"
        ),
        "default_buffer_days": _optional_nonnegative_int(
            buffer_days, "Buffer"
        ),
    }
    before = {
        "default_manufacturing_lead_days": supplier.default_manufacturing_lead_days,
        "default_transit_lead_days": supplier.default_transit_lead_days,
        "default_buffer_days": supplier.default_buffer_days,
    }

    supplier.default_manufacturing_lead_days = after[
        "default_manufacturing_lead_days"
    ]
    supplier.default_transit_lead_days = after["default_transit_lead_days"]
    supplier.default_buffer_days = after["default_buffer_days"]

    if before != after:
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action="supplier.default_lead_times.updated",
                entity_type="supplier",
                entity_id=str(supplier.supplier_id),
                source="web",
                summary=f"Updated default lead times for {supplier.display_name}.",
                before_json=json.dumps(before, sort_keys=True),
                after_json=json.dumps(after, sort_keys=True),
            )
        )
    session.flush()
    return supplier


def set_supplier_item_settings(
    session: Session,
    *,
    supplier_id: uuid.UUID,
    item_id: uuid.UUID,
    is_linked: bool,
    is_preferred: bool,
    supplier_item_number: str,
    minimum_order_quantity: Decimal | str | None,
    manufacturing_lead_days_override: int | str | None,
    transit_lead_days_override: int | str | None,
    buffer_days_override: int | str | None,
    actor_user_id: uuid.UUID,
) -> ItemSupplier | None:
    supplier = session.get(Supplier, supplier_id)
    item = session.get(Item, item_id)
    if supplier is None:
        raise LookupError("Supplier not found.")
    if item is None:
        raise LookupError("Item not found.")

    link = session.scalar(
        select(ItemSupplier).where(
            ItemSupplier.supplier_id == supplier_id,
            ItemSupplier.item_id == item_id,
        )
    )
    before = (
        {
            "match_status": link.match_status,
            "match_method": link.match_method,
            "is_preferred": link.is_preferred,
            "supplier_item_number": link.supplier_item_number,
            "minimum_order_quantity": (
                str(link.minimum_order_quantity)
                if link.minimum_order_quantity is not None
                else None
            ),
            "manufacturing_lead_days_override": (
                link.manufacturing_lead_days_override
            ),
            "transit_lead_days_override": link.transit_lead_days_override,
            "buffer_days_override": link.buffer_days_override,
        }
        if link is not None
        else None
    )

    if not is_linked:
        if link is None:
            return None
        link.match_status = "rejected"
        link.match_method = "user"
        link.is_preferred = False
        after = {
            **(before or {}),
            "match_status": "rejected",
            "match_method": "user",
            "is_preferred": False,
        }
        action = "supplier.item.unlinked"
        summary = f"Unlinked {item.item_number} from {supplier.display_name}."
    else:
        minimum = _optional_nonnegative_decimal(
            minimum_order_quantity, "Minimum order quantity"
        )
        manufacturing = _optional_nonnegative_int(
            manufacturing_lead_days_override,
            "Manufacturing lead-time override",
        )
        transit = _optional_nonnegative_int(
            transit_lead_days_override,
            "Transit lead-time override",
        )
        buffer = _optional_nonnegative_int(
            buffer_days_override,
            "Buffer override",
        )

        other_preferred_ids: list[str] = []
        if is_preferred:
            for other in session.scalars(
                select(ItemSupplier).where(
                    ItemSupplier.item_id == item_id,
                    ItemSupplier.supplier_id != supplier_id,
                    ItemSupplier.is_preferred == true(),
                )
            ):
                other.is_preferred = False
                other_preferred_ids.append(str(other.item_supplier_id))

        if link is None:
            link = ItemSupplier(
                item_id=item_id,
                supplier_id=supplier_id,
                match_status="approved",
                match_method="user",
            )
            session.add(link)

        link.match_status = "approved"
        link.match_method = "user"
        link.is_preferred = bool(is_preferred)
        link.supplier_item_number = supplier_item_number.strip() or None
        link.minimum_order_quantity = minimum
        link.manufacturing_lead_days_override = manufacturing
        link.transit_lead_days_override = transit
        link.buffer_days_override = buffer

        latest = session.execute(
            select(
                PurchaseLine.transaction_date,
                PurchaseLine.unit_price,
                PurchaseLine.currency_code,
            )
            .select_from(PurchaseLine)
            .join(
                PurchaseDocument,
                PurchaseDocument.purchase_document_id
                == PurchaseLine.purchase_document_id,
            )
            .join(
                ImportBatch,
                ImportBatch.import_batch_id == PurchaseLine.last_import_batch_id,
            )
            .join(
                Supplier,
                Supplier.supplier_id == PurchaseDocument.supplier_id,
            )
            .where(
                PurchaseDocument.supplier_id == supplier_id,
                PurchaseLine.item_id == item_id,
                *purchase_bill_conditions(positive_quantity_only=True),
                real_supplier_condition(),
            )
            .order_by(
                PurchaseLine.transaction_date.desc(),
                PurchaseDocument.purchase_no.desc(),
                PurchaseLine.line_sequence.desc(),
            )
            .limit(1)
        ).first()
        if latest is not None:
            link.last_purchase_date = latest[0]
            link.last_purchase_price = latest[1]
            link.last_purchase_currency = latest[2]

        after = {
            "match_status": link.match_status,
            "match_method": link.match_method,
            "is_preferred": link.is_preferred,
            "supplier_item_number": link.supplier_item_number,
            "minimum_order_quantity": (
                str(link.minimum_order_quantity)
                if link.minimum_order_quantity is not None
                else None
            ),
            "manufacturing_lead_days_override": (
                link.manufacturing_lead_days_override
            ),
            "transit_lead_days_override": link.transit_lead_days_override,
            "buffer_days_override": link.buffer_days_override,
            "other_preferred_links_cleared": other_preferred_ids,
        }
        action = "supplier.item.settings.updated"
        summary = f"Updated {supplier.display_name} settings for {item.item_number}."

    if before != after:
        session.flush()
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action=action,
                entity_type="item_supplier",
                entity_id=str(link.item_supplier_id),
                source="web",
                summary=summary,
                before_json=json.dumps(before, sort_keys=True) if before else None,
                after_json=json.dumps(after, sort_keys=True),
            )
        )
    session.flush()
    return link
