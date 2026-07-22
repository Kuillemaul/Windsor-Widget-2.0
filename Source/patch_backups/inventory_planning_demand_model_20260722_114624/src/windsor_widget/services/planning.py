"""Explainable inventory and demand planning read models.

This module deliberately keeps the planning calculations read-only.  The inventory
snapshot's ``Available`` value is authoritative and already represents:

    On Hand - Committed + On Order

Current cover-order demand is shown as a reconciliation against MYOB Committed; it is
not added to demand a second time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from sqlalchemy import case, extract, func, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    CoverOrderDocument,
    CoverOrderLine,
    CoverOrderSnapshot,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    ItemSupplier,
    PurchaseDocument,
    PurchaseLine,
    SalesLine,
    Supplier,
)

_ZERO = Decimal("0")
_DAYS_PER_MONTH = Decimal("30.4375")
TrendMode = Literal["3v3", "6v6", "yoy"]
PlanningStatus = Literal["order", "watch", "ok", "no_inventory"]


class PlanningLookupError(LookupError):
    """Raised when an item or required planning snapshot cannot be resolved."""


@dataclass(frozen=True, slots=True)
class InventoryPosition:
    inventory_snapshot_id: uuid.UUID
    captured_at: datetime
    source_file_name: str
    on_hand: Decimal
    committed: Decimal
    on_order: Decimal
    available: Decimal


@dataclass(frozen=True, slots=True)
class TrendComparison:
    mode: TrendMode
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date
    current_total: Decimal
    previous_total: Decimal
    current_average: Decimal
    previous_average: Decimal
    delta: Decimal
    percent_change: Decimal | None
    significant: bool
    lead_adjustment_raw: Decimal
    lead_adjustment_rounded: Decimal


@dataclass(frozen=True, slots=True)
class PurchaseContext:
    supplier_id: uuid.UUID
    supplier_name: str
    purchase_no: str
    transaction_date: date
    quantity: Decimal
    unit_price: Decimal
    currency_code: str | None


@dataclass(frozen=True, slots=True)
class ItemPlanningAnalysis:
    item_id: uuid.UUID
    item_number: str
    item_name: str
    as_of_date: date
    analysis_start: date
    analysis_end: date
    analysis_months: int
    sales_quantity: Decimal
    average_monthly_sales: Decimal
    current_cover_quantity: Decimal
    cover_committed_delta: Decimal
    inventory: InventoryPosition | None
    lead_days: int
    lead_time_source: str
    lead_demand: Decimal
    minimum_level: Decimal
    target_stock: Decimal
    reorder_multiple: Decimal
    minimum_order_quantity: Decimal
    suggested_order_raw: Decimal
    suggested_order: Decimal
    trend: TrendComparison
    adjusted_suggested_order: Decimal
    status: PlanningStatus
    reasons: tuple[str, ...]
    data_gaps: tuple[str, ...]
    latest_purchase: PurchaseContext | None


@dataclass(frozen=True, slots=True)
class OrderAnalysisRow:
    item_number: str
    item_name: str
    status: PlanningStatus
    sales_quantity: Decimal
    average_monthly_sales: Decimal
    on_hand: Decimal
    committed: Decimal
    on_order: Decimal
    available: Decimal
    current_cover_quantity: Decimal
    lead_days: int
    lead_demand: Decimal
    target_stock: Decimal
    suggested_order: Decimal
    trend_adjustment: Decimal
    adjusted_suggested_order: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class OrderAnalysisResult:
    as_of_date: date
    analysis_start: date
    analysis_end: date
    inventory_captured_at: datetime
    inventory_source_file_name: str
    considered_items: int
    flagged_items: int
    rows: tuple[OrderAnalysisRow, ...]


@dataclass(frozen=True, slots=True)
class PlanningReadiness:
    inventory_snapshot_id: uuid.UUID | None
    inventory_captured_at: datetime | None
    inventory_source_file_name: str | None
    inventory_row_count: int
    active_inventoried_items: int
    active_inventoried_items_with_snapshot: int
    active_inventoried_items_missing_snapshot: int
    preferred_supplier_links: int
    configured_supplier_lead_times: int
    current_cover_order_snapshots: int
    gaps: tuple[str, ...]


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> date:
    base = _month_start(value)
    month_index = base.year * 12 + (base.month - 1) + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _completed_window(as_of_date: date, months: int) -> tuple[date, date]:
    if months < 1 or months > 120:
        raise ValueError("analysis months must be between 1 and 120")
    current_month = _month_start(as_of_date)
    end_date = current_month - timedelta(days=1)
    start_date = _shift_month(current_month, -months)
    return start_date, end_date


def _trend_windows(as_of_date: date, mode: TrendMode) -> tuple[date, date, date, date, int]:
    current_month = _month_start(as_of_date)
    comparison_months = 3 if mode == "3v3" else 6 if mode == "6v6" else 12
    current_start = _shift_month(current_month, -comparison_months)
    current_end = current_month - timedelta(days=1)
    previous_start = _shift_month(current_month, -(comparison_months * 2))
    previous_end = current_start - timedelta(days=1)
    return current_start, current_end, previous_start, previous_end, comparison_months


def _is_significant(delta: Decimal, previous_total: Decimal, current_total: Decimal) -> bool:
    minimum_units = Decimal("10")
    if abs(delta) < minimum_units:
        return False
    if previous_total == 0:
        return abs(current_total) >= minimum_units
    return abs(delta) >= max(minimum_units, abs(previous_total) * Decimal("0.25"))


def _round_up_positive(
    quantity: Decimal,
    *,
    multiple: Decimal,
    minimum_order_quantity: Decimal = _ZERO,
) -> Decimal:
    if quantity <= 0:
        return _ZERO
    working = max(quantity, minimum_order_quantity)
    if multiple <= 0:
        multiple = Decimal("1")
    return (working / multiple).to_integral_value(rounding=ROUND_CEILING) * multiple


def _round_signed(quantity: Decimal, *, multiple: Decimal) -> Decimal:
    if quantity == 0:
        return _ZERO
    sign = Decimal("1") if quantity > 0 else Decimal("-1")
    return sign * _round_up_positive(abs(quantity), multiple=multiple)


def _monthly_sales_map(
    session: Session,
    *,
    item_id: uuid.UUID,
    start_date: date,
    end_date: date,
) -> dict[date, Decimal]:
    year_expr = extract("year", SalesLine.transaction_date)
    month_expr = extract("month", SalesLine.transaction_date)
    rows = session.execute(
        select(
            year_expr,
            month_expr,
            func.coalesce(func.sum(SalesLine.quantity), 0),
        )
        .where(
            SalesLine.item_id == item_id,
            SalesLine.is_active == true(),
            SalesLine.transaction_date >= start_date,
            SalesLine.transaction_date <= end_date,
        )
        .group_by(year_expr, month_expr)
        .order_by(year_expr, month_expr)
    )
    return {
        date(int(year), int(month), 1): _decimal(quantity)
        for year, month, quantity in rows
    }


def _sum_months(values: dict[date, Decimal], start_date: date, months: int) -> Decimal:
    return sum(
        (values.get(_shift_month(start_date, offset), _ZERO) for offset in range(months)),
        _ZERO,
    )


def _trend_comparison(
    monthly_values: dict[date, Decimal],
    *,
    as_of_date: date,
    mode: TrendMode,
    lead_days: int,
    reorder_multiple: Decimal,
) -> TrendComparison:
    current_start, current_end, previous_start, previous_end, months = _trend_windows(
        as_of_date, mode
    )
    current_total = _sum_months(monthly_values, current_start, months)
    previous_total = _sum_months(monthly_values, previous_start, months)
    divisor = Decimal(months)
    current_average = current_total / divisor
    previous_average = previous_total / divisor
    delta = current_total - previous_total
    percent_change = None if previous_total == 0 else (delta / previous_total) * 100
    lead_adjustment_raw = (
        (current_average - previous_average) * Decimal(lead_days) / _DAYS_PER_MONTH
    )
    return TrendComparison(
        mode=mode,
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
        current_total=current_total,
        previous_total=previous_total,
        current_average=current_average,
        previous_average=previous_average,
        delta=delta,
        percent_change=percent_change,
        significant=_is_significant(delta, previous_total, current_total),
        lead_adjustment_raw=lead_adjustment_raw,
        lead_adjustment_rounded=_round_signed(
            lead_adjustment_raw, multiple=reorder_multiple
        ),
    )


def _current_inventory(
    session: Session, item_id: uuid.UUID
) -> tuple[InventorySnapshot, InventorySnapshotLine] | None:
    return session.execute(
        select(InventorySnapshot, InventorySnapshotLine)
        .join(
            InventorySnapshotLine,
            InventorySnapshotLine.inventory_snapshot_id
            == InventorySnapshot.inventory_snapshot_id,
        )
        .where(
            InventorySnapshot.is_current == true(),
            InventorySnapshotLine.item_id == item_id,
        )
        .limit(1)
    ).one_or_none()


def _current_cover_quantity(session: Session, item_id: uuid.UUID) -> Decimal:
    value = session.scalar(
        select(func.coalesce(func.sum(CoverOrderLine.quantity), 0))
        .select_from(CoverOrderLine)
        .join(
            CoverOrderDocument,
            CoverOrderDocument.cover_order_document_id
            == CoverOrderLine.cover_order_document_id,
        )
        .join(
            CoverOrderSnapshot,
            CoverOrderSnapshot.cover_order_snapshot_id
            == CoverOrderDocument.cover_order_snapshot_id,
        )
        .where(
            CoverOrderSnapshot.is_current == true(),
            CoverOrderLine.item_id == item_id,
        )
    )
    return _decimal(value)


def _preferred_supplier(
    session: Session, item_id: uuid.UUID
) -> tuple[ItemSupplier, Supplier] | None:
    approved_first = case((ItemSupplier.match_status == "approved", 0), else_=1)
    return session.execute(
        select(ItemSupplier, Supplier)
        .join(Supplier, Supplier.supplier_id == ItemSupplier.supplier_id)
        .where(
            ItemSupplier.item_id == item_id,
            ItemSupplier.is_preferred == true(),
        )
        .order_by(approved_first, Supplier.display_name)
        .limit(1)
    ).one_or_none()


def _latest_purchase(
    session: Session, item_id: uuid.UUID
) -> tuple[PurchaseContext | None, Supplier | None]:
    row = session.execute(
        select(PurchaseLine, PurchaseDocument, Supplier)
        .join(
            PurchaseDocument,
            PurchaseDocument.purchase_document_id == PurchaseLine.purchase_document_id,
        )
        .join(Supplier, Supplier.supplier_id == PurchaseDocument.supplier_id)
        .where(PurchaseLine.item_id == item_id, PurchaseLine.is_active == true())
        .order_by(PurchaseLine.transaction_date.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        return None, None
    line, document, supplier = row
    return (
        PurchaseContext(
            supplier_id=supplier.supplier_id,
            supplier_name=supplier.display_name,
            purchase_no=document.purchase_no,
            transaction_date=line.transaction_date,
            quantity=_decimal(line.quantity),
            unit_price=_decimal(line.unit_price),
            currency_code=line.currency_code,
        ),
        supplier,
    )


def _lead_days(
    preferred: tuple[ItemSupplier, Supplier] | None,
    latest_supplier: Supplier | None,
    *,
    fallback_lead_days: int,
) -> tuple[int, str, Decimal, Decimal]:
    link = preferred[0] if preferred else None
    supplier = preferred[1] if preferred else latest_supplier
    values: list[int] = []
    if link is not None:
        for override, default in (
            (
                link.manufacturing_lead_days_override,
                supplier.default_manufacturing_lead_days if supplier else None,
            ),
            (
                link.transit_lead_days_override,
                supplier.default_transit_lead_days if supplier else None,
            ),
            (
                link.buffer_days_override,
                supplier.default_buffer_days if supplier else None,
            ),
        ):
            selected = override if override is not None else default
            if selected is not None:
                values.append(int(selected))
    elif supplier is not None:
        values.extend(
            int(value)
            for value in (
                supplier.default_manufacturing_lead_days,
                supplier.default_transit_lead_days,
                supplier.default_buffer_days,
            )
            if value is not None
        )

    if values:
        source = "preferred supplier" if preferred else "latest purchase supplier"
        lead_days = max(0, sum(values))
    else:
        lead_days = max(1, int(fallback_lead_days))
        source = f"fallback {lead_days} days"

    minimum_order_quantity = _decimal(link.minimum_order_quantity if link else None)
    return lead_days, source, _ZERO, minimum_order_quantity


def _position(snapshot: InventorySnapshot, line: InventorySnapshotLine) -> InventoryPosition:
    return InventoryPosition(
        inventory_snapshot_id=snapshot.inventory_snapshot_id,
        captured_at=snapshot.captured_at,
        source_file_name=snapshot.source_file_name,
        on_hand=_decimal(line.on_hand),
        committed=_decimal(line.committed),
        on_order=_decimal(line.on_order),
        available=_decimal(line.available),
    )


def get_item_planning_analysis(
    session: Session,
    item_number: str,
    *,
    analysis_months: int = 12,
    fallback_lead_weeks: int = 14,
    trend_mode: TrendMode = "3v3",
    as_of_date: date | None = None,
) -> ItemPlanningAnalysis:
    """Return an explainable planning analysis for one exact item number."""

    if trend_mode not in {"3v3", "6v6", "yoy"}:
        raise ValueError("trend mode must be 3v3, 6v6 or yoy")
    as_of = as_of_date or date.today()
    analysis_start, analysis_end = _completed_window(as_of, analysis_months)
    item = session.scalar(select(Item).where(Item.item_number == item_number.strip()))
    if item is None:
        raise PlanningLookupError(f"No item exists with item number {item_number.strip()!r}.")

    trend_start = _trend_windows(as_of, trend_mode)[2]
    monthly_start = min(analysis_start, trend_start)
    monthly = _monthly_sales_map(
        session,
        item_id=item.item_id,
        start_date=monthly_start,
        end_date=analysis_end,
    )
    sales_quantity = _sum_months(monthly, analysis_start, analysis_months)
    average_monthly_sales = sales_quantity / Decimal(analysis_months)

    inventory_row = _current_inventory(session, item.item_id)
    inventory = _position(*inventory_row) if inventory_row else None
    current_cover = _current_cover_quantity(session, item.item_id)
    latest_purchase, latest_supplier = _latest_purchase(session, item.item_id)
    preferred = _preferred_supplier(session, item.item_id)
    lead_days, lead_source, link_reorder_multiple, minimum_order_quantity = _lead_days(
        preferred,
        latest_supplier,
        fallback_lead_days=max(1, int(fallback_lead_weeks)) * 7,
    )
    reorder_multiple = link_reorder_multiple
    if reorder_multiple <= 0:
        reorder_multiple = _decimal(item.reorder_quantity)
    if reorder_multiple <= 0:
        reorder_multiple = Decimal("1")

    trend = _trend_comparison(
        monthly,
        as_of_date=as_of,
        mode=trend_mode,
        lead_days=lead_days,
        reorder_multiple=reorder_multiple,
    )
    lead_demand = average_monthly_sales * Decimal(lead_days) / _DAYS_PER_MONTH
    minimum_level = max(_ZERO, _decimal(item.minimum_level))
    target_stock = max(lead_demand, minimum_level)

    reasons: list[str] = []
    gaps: list[str] = [
        "Dated inbound/container ETA data is not modelled yet, so at-risk timing is not calculated."
    ]
    if lead_source.startswith("fallback"):
        gaps.append(
            f"No configured supplier lead time was found; {lead_source} is being used."
        )

    if inventory is None:
        suggested_raw = _ZERO
        suggested_order = _ZERO
        adjusted = _ZERO
        cover_delta = current_cover
        status: PlanningStatus = "no_inventory"
        reasons.append("The item is missing from the current inventory snapshot.")
    else:
        cover_delta = current_cover - inventory.committed
        suggested_raw = max(_ZERO, target_stock - inventory.available)
        suggested_order = _round_up_positive(
            suggested_raw,
            multiple=reorder_multiple,
            minimum_order_quantity=minimum_order_quantity,
        )
        adjusted_raw = max(_ZERO, suggested_raw + trend.lead_adjustment_raw)
        adjusted = _round_up_positive(
            adjusted_raw,
            multiple=reorder_multiple,
            minimum_order_quantity=minimum_order_quantity,
        )
        if inventory.available < 0:
            reasons.append(f"Available stock is negative ({inventory.available}).")
        if suggested_order > 0:
            reasons.append(
                f"Available {inventory.available} is below target stock {target_stock:.2f}."
            )
        if trend.significant and trend.delta > 0:
            percent_text = (
                "new demand"
                if trend.percent_change is None
                else f"{trend.percent_change:.1f}%"
            )
            reasons.append(
                f"{trend.mode} demand increased by {trend.delta} units ({percent_text})."
            )
        if abs(cover_delta) > Decimal("0.000001"):
            reasons.append(
                f"Current cover quantity differs from MYOB Committed by {cover_delta}."
            )

        if adjusted > 0 or inventory.available < 0:
            status = "order"
        elif trend.significant and trend.delta > 0:
            status = "watch"
        else:
            status = "ok"
            reasons.append("Available stock meets the current lead-time target.")

        snapshot_age = max(0, (as_of - inventory.captured_at.date()).days)
        if snapshot_age > 7:
            gaps.append(f"The current inventory snapshot is {snapshot_age} days old.")

    return ItemPlanningAnalysis(
        item_id=item.item_id,
        item_number=item.item_number,
        item_name=item.item_name,
        as_of_date=as_of,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
        analysis_months=analysis_months,
        sales_quantity=sales_quantity,
        average_monthly_sales=average_monthly_sales,
        current_cover_quantity=current_cover,
        cover_committed_delta=cover_delta,
        inventory=inventory,
        lead_days=lead_days,
        lead_time_source=lead_source,
        lead_demand=lead_demand,
        minimum_level=minimum_level,
        target_stock=target_stock,
        reorder_multiple=reorder_multiple,
        minimum_order_quantity=minimum_order_quantity,
        suggested_order_raw=suggested_raw,
        suggested_order=suggested_order,
        trend=trend,
        adjusted_suggested_order=adjusted,
        status=status,
        reasons=tuple(reasons),
        data_gaps=tuple(gaps),
        latest_purchase=latest_purchase,
    )


def _bulk_monthly_sales(
    session: Session,
    *,
    start_date: date,
    end_date: date,
) -> dict[uuid.UUID, dict[date, Decimal]]:
    year_expr = extract("year", SalesLine.transaction_date)
    month_expr = extract("month", SalesLine.transaction_date)
    rows = session.execute(
        select(
            SalesLine.item_id,
            year_expr,
            month_expr,
            func.coalesce(func.sum(SalesLine.quantity), 0),
        )
        .where(
            SalesLine.item_id.is_not(None),
            SalesLine.is_active == true(),
            SalesLine.transaction_date >= start_date,
            SalesLine.transaction_date <= end_date,
        )
        .group_by(SalesLine.item_id, year_expr, month_expr)
    )
    result: dict[uuid.UUID, dict[date, Decimal]] = {}
    for item_id, year, month, quantity in rows:
        result.setdefault(item_id, {})[date(int(year), int(month), 1)] = _decimal(
            quantity
        )
    return result


def _bulk_cover_quantities(session: Session) -> dict[uuid.UUID, Decimal]:
    rows = session.execute(
        select(
            CoverOrderLine.item_id,
            func.coalesce(func.sum(CoverOrderLine.quantity), 0),
        )
        .select_from(CoverOrderLine)
        .join(
            CoverOrderDocument,
            CoverOrderDocument.cover_order_document_id
            == CoverOrderLine.cover_order_document_id,
        )
        .join(
            CoverOrderSnapshot,
            CoverOrderSnapshot.cover_order_snapshot_id
            == CoverOrderDocument.cover_order_snapshot_id,
        )
        .where(
            CoverOrderSnapshot.is_current == true(),
            CoverOrderLine.item_id.is_not(None),
        )
        .group_by(CoverOrderLine.item_id)
    )
    return {item_id: _decimal(quantity) for item_id, quantity in rows}


def get_order_analysis(
    session: Session,
    *,
    analysis_months: int = 12,
    fallback_lead_weeks: int = 14,
    trend_mode: TrendMode = "3v3",
    as_of_date: date | None = None,
    limit: int = 100,
    include_ok: bool = False,
) -> OrderAnalysisResult:
    """Return the first all-item Order Analysis read model for the future UI."""

    if trend_mode not in {"3v3", "6v6", "yoy"}:
        raise ValueError("trend mode must be 3v3, 6v6 or yoy")
    limit = max(1, min(int(limit), 2_000))
    as_of = as_of_date or date.today()
    analysis_start, analysis_end = _completed_window(as_of, analysis_months)
    snapshot = session.scalar(
        select(InventorySnapshot)
        .where(InventorySnapshot.is_current == true())
        .order_by(InventorySnapshot.captured_at.desc())
        .limit(1)
    )
    if snapshot is None:
        raise PlanningLookupError(
            "No current inventory snapshot exists. Preview and commit zinvs1.xlsx first."
        )

    inventory_rows = list(
        session.execute(
            select(Item, InventorySnapshotLine)
            .join(
                InventorySnapshotLine,
                InventorySnapshotLine.item_id == Item.item_id,
            )
            .where(
                InventorySnapshotLine.inventory_snapshot_id
                == snapshot.inventory_snapshot_id,
                Item.is_active == true(),
                Item.is_inventoried == true(),
                Item.excluded_from_item_view != true(),
            )
            .order_by(Item.item_number)
        )
    )
    trend_start = _trend_windows(as_of, trend_mode)[2]
    monthly_start = min(analysis_start, trend_start)
    sales_by_item = _bulk_monthly_sales(
        session, start_date=monthly_start, end_date=analysis_end
    )
    cover_by_item = _bulk_cover_quantities(session)
    lead_days = max(1, int(fallback_lead_weeks)) * 7

    rows: list[OrderAnalysisRow] = []
    for item, inventory_line in inventory_rows:
        monthly = sales_by_item.get(item.item_id, {})
        sales_quantity = _sum_months(monthly, analysis_start, analysis_months)
        average = sales_quantity / Decimal(analysis_months)
        reorder_multiple = _decimal(item.reorder_quantity)
        if reorder_multiple <= 0:
            reorder_multiple = Decimal("1")
        trend = _trend_comparison(
            monthly,
            as_of_date=as_of,
            mode=trend_mode,
            lead_days=lead_days,
            reorder_multiple=reorder_multiple,
        )
        lead_demand = average * Decimal(lead_days) / _DAYS_PER_MONTH
        target = max(lead_demand, max(_ZERO, _decimal(item.minimum_level)))
        available = _decimal(inventory_line.available)
        suggested_raw = max(_ZERO, target - available)
        suggested = _round_up_positive(
            suggested_raw, multiple=reorder_multiple
        )
        adjusted = _round_up_positive(
            max(_ZERO, suggested_raw + trend.lead_adjustment_raw),
            multiple=reorder_multiple,
        )
        current_cover = cover_by_item.get(item.item_id, _ZERO)

        reason_parts: list[str] = []
        if available < 0:
            reason_parts.append(f"available {available} is negative")
        if adjusted > 0:
            reason_parts.append(f"available {available} is below target {target:.2f}")
        if trend.significant and trend.delta > 0:
            reason_parts.append(f"{trend_mode} demand increased by {trend.delta}")
        if adjusted > 0 or available < 0:
            status: PlanningStatus = "order"
        elif trend.significant and trend.delta > 0:
            status = "watch"
        else:
            status = "ok"
        if not reason_parts:
            reason_parts.append("stock meets the current target")

        if include_ok or status != "ok":
            rows.append(
                OrderAnalysisRow(
                    item_number=item.item_number,
                    item_name=item.item_name,
                    status=status,
                    sales_quantity=sales_quantity,
                    average_monthly_sales=average,
                    on_hand=_decimal(inventory_line.on_hand),
                    committed=_decimal(inventory_line.committed),
                    on_order=_decimal(inventory_line.on_order),
                    available=available,
                    current_cover_quantity=current_cover,
                    lead_days=lead_days,
                    lead_demand=lead_demand,
                    target_stock=target,
                    suggested_order=suggested,
                    trend_adjustment=trend.lead_adjustment_rounded,
                    adjusted_suggested_order=adjusted,
                    reason="; ".join(reason_parts),
                )
            )

    priority = {"order": 0, "watch": 1, "no_inventory": 2, "ok": 3}
    rows.sort(
        key=lambda row: (
            priority[row.status],
            -row.adjusted_suggested_order,
            row.available,
            row.item_number.casefold(),
        )
    )
    flagged_items = sum(1 for row in rows if row.status != "ok")
    return OrderAnalysisResult(
        as_of_date=as_of,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
        inventory_captured_at=snapshot.captured_at,
        inventory_source_file_name=snapshot.source_file_name,
        considered_items=len(inventory_rows),
        flagged_items=flagged_items,
        rows=tuple(rows[:limit]),
    )


def get_planning_readiness(session: Session) -> PlanningReadiness:
    """Return the remaining data gaps before Order Analysis UI construction."""

    snapshot = session.scalar(
        select(InventorySnapshot)
        .where(InventorySnapshot.is_current == true())
        .order_by(InventorySnapshot.captured_at.desc())
        .limit(1)
    )
    active_inventoried = int(
        session.scalar(
            select(func.count(Item.item_id)).where(
                Item.is_active == true(),
                Item.is_inventoried == true(),
                Item.excluded_from_item_view != true(),
            )
        )
        or 0
    )
    with_snapshot = 0
    inventory_row_count = 0
    if snapshot is not None:
        inventory_row_count = int(
            session.scalar(
                select(func.count(InventorySnapshotLine.inventory_snapshot_line_id)).where(
                    InventorySnapshotLine.inventory_snapshot_id
                    == snapshot.inventory_snapshot_id
                )
            )
            or 0
        )
        with_snapshot = int(
            session.scalar(
                select(func.count(InventorySnapshotLine.inventory_snapshot_line_id))
                .select_from(InventorySnapshotLine)
                .join(Item, Item.item_id == InventorySnapshotLine.item_id)
                .where(
                    InventorySnapshotLine.inventory_snapshot_id
                    == snapshot.inventory_snapshot_id,
                    Item.is_active == true(),
                    Item.is_inventoried == true(),
                    Item.excluded_from_item_view != true(),
                )
            )
            or 0
        )

    preferred_links = list(
        session.execute(
            select(ItemSupplier, Supplier)
            .join(Supplier, Supplier.supplier_id == ItemSupplier.supplier_id)
            .where(ItemSupplier.is_preferred == true())
        )
    )
    configured_leads = 0
    for link, supplier in preferred_links:
        if any(
            value is not None
            for value in (
                link.manufacturing_lead_days_override,
                link.transit_lead_days_override,
                link.buffer_days_override,
                supplier.default_manufacturing_lead_days,
                supplier.default_transit_lead_days,
                supplier.default_buffer_days,
            )
        ):
            configured_leads += 1

    current_cover_count = int(
        session.scalar(
            select(func.count(CoverOrderSnapshot.cover_order_snapshot_id)).where(
                CoverOrderSnapshot.is_current == true()
            )
        )
        or 0
    )
    gaps: list[str] = []
    if snapshot is None:
        gaps.append("No current inventory snapshot has been committed.")
    missing = max(0, active_inventoried - with_snapshot)
    if missing:
        gaps.append(
            f"{missing} active inventoried item(s) are missing from the current snapshot."
        )
    if not preferred_links:
        gaps.append(
            "Preferred item-supplier links have not been built; Order Analysis uses the "
            "fallback lead time."
        )
    elif configured_leads < len(preferred_links):
        gaps.append(
            f"{len(preferred_links) - configured_leads} preferred supplier link(s) lack "
            "configured lead times."
        )
    if current_cover_count != 1:
        gaps.append(
            f"Exactly one current cover-order snapshot is required; found {current_cover_count}."
        )
    gaps.append(
        "Dated inbound/container ETA data is still required for reliable at-risk timing."
    )

    return PlanningReadiness(
        inventory_snapshot_id=(snapshot.inventory_snapshot_id if snapshot else None),
        inventory_captured_at=(snapshot.captured_at if snapshot else None),
        inventory_source_file_name=(snapshot.source_file_name if snapshot else None),
        inventory_row_count=inventory_row_count,
        active_inventoried_items=active_inventoried,
        active_inventoried_items_with_snapshot=with_snapshot,
        active_inventoried_items_missing_snapshot=missing,
        preferred_supplier_links=len(preferred_links),
        configured_supplier_lead_times=configured_leads,
        current_cover_order_snapshots=current_cover_count,
        gaps=tuple(gaps),
    )
