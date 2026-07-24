"""Observed purchasing, receiving and sales-behaviour analytics.

This service is deliberately read-only. It uses genuine MYOB purchase bills,
invoiced sales, current inventory and explicit customer cover to describe the
historical operating pattern. The result is advisory and does not yet replace
the existing planning recommendation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import sqrt

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    CoverOrderDocument,
    CoverOrderLine,
    CoverOrderSnapshot,
    ImportBatch,
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
from windsor_widget.services.purchase_bill_rules import (
    purchase_bill_conditions,
    real_supplier_condition,
)

_ZERO = Decimal("0")
_DAYS_PER_MONTH = Decimal("30.4375")
_DEFAULT_WAVE_GAP_DAYS = 10


@dataclass(frozen=True, slots=True)
class ReceivingWave:
    start_date: date
    end_date: date
    document_count: int
    item_count: int
    line_count: int
    total_quantity: Decimal
    total_value: Decimal


@dataclass(frozen=True, slots=True)
class SupplierReceivingBehavior:
    supplier_id: uuid.UUID
    supplier_name: str
    as_of_date: date
    wave_gap_days: int
    wave_count: int
    first_wave_date: date | None
    latest_wave_date: date | None
    median_interval_days: int | None
    average_interval_days: Decimal | None
    minimum_interval_days: int | None
    maximum_interval_days: int | None
    consistency: str
    confidence: str
    next_observed_cycle_date: date | None
    recent_waves: tuple[ReceivingWave, ...]


@dataclass(frozen=True, slots=True)
class ItemReplenishmentBehavior:
    item_id: uuid.UUID
    item_number: str
    item_name: str
    replenishment_policy: str
    as_of_date: date
    demand_start: date
    demand_end: date
    demand_months: int
    demand_pattern: str
    average_monthly_sales: Decimal
    active_sales_months: int
    sales_event_count: int
    typical_sales_event_quantity: Decimal
    average_sales_event_quantity: Decimal
    sales_interval_days: int | None
    purchase_supplier_id: uuid.UUID | None
    purchase_supplier_name: str | None
    supplier_source: str
    purchase_event_count: int
    first_purchase_date: date | None
    last_purchase_date: date | None
    typical_purchase_quantity: Decimal
    average_purchase_quantity: Decimal
    purchase_interval_days: int | None
    purchase_consistency: str
    supplier_wave_count: int
    supplier_receipt_interval_days: int | None
    supplier_receipt_consistency: str
    last_supplier_wave_date: date | None
    next_observed_cycle_date: date | None
    lead_days: int
    lead_source: str
    observed_batch_cover_months: Decimal | None
    observed_coverage_days: int
    observed_cycle_demand: Decimal
    current_on_hand: Decimal
    current_on_order: Decimal
    projected_pool: Decimal
    explicit_customer_cover: Decimal
    behavioural_requirement: Decimal
    behavioural_gap: Decimal
    confidence: str
    notes: tuple[str, ...]


@dataclass(slots=True)
class _BillDocument:
    document_id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    transaction_date: date
    item_ids: set[uuid.UUID]
    line_count: int = 0
    quantity: Decimal = _ZERO
    value: Decimal = _ZERO


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return _ZERO
    return sum(values, _ZERO) / Decimal(len(values))


def _median_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return _ZERO
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _median_days(values: list[int]) -> int | None:
    if not values:
        return None
    median_value = _median_decimal([Decimal(value) for value in values])
    return int(median_value.to_integral_value(rounding=ROUND_HALF_UP))


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> date:
    base = _month_start(value)
    month_index = base.year * 12 + base.month - 1 + offset
    year, zero_month = divmod(month_index, 12)
    return date(year, zero_month + 1, 1)


def _completed_window(as_of_date: date, months: int) -> tuple[date, date]:
    if months < 3 or months > 120:
        raise ValueError("demand months must be between 3 and 120")
    current_month = _month_start(as_of_date)
    return _shift_month(current_month, -months), current_month - timedelta(days=1)


def _intervals(dates: list[date]) -> list[int]:
    ordered = sorted(set(dates))
    return [
        (ordered[index] - ordered[index - 1]).days
        for index in range(1, len(ordered))
    ]


def _interval_summary(dates: list[date]) -> tuple[
    int | None,
    Decimal | None,
    int | None,
    int | None,
    str,
]:
    intervals = _intervals(dates)
    if not intervals:
        return None, None, None, None, "Insufficient history"

    median_days = _median_days(intervals)
    average_days = _average([Decimal(value) for value in intervals])
    minimum = min(intervals)
    maximum = max(intervals)

    if len(intervals) < 2 or not median_days:
        consistency = "Insufficient history"
    else:
        deviations = [abs(value - median_days) for value in intervals]
        mad = _median_days(deviations) or 0
        ratio = Decimal(mad) / Decimal(max(1, median_days))
        if ratio <= Decimal("0.15"):
            consistency = "Regular"
        elif ratio <= Decimal("0.35"):
            consistency = "Moderately regular"
        else:
            consistency = "Variable"

    return median_days, average_days, minimum, maximum, consistency


def _confidence(event_count: int, interval_count: int) -> str:
    if event_count >= 5 and interval_count >= 4:
        return "High"
    if event_count >= 3 and interval_count >= 2:
        return "Medium"
    if event_count >= 2:
        return "Low"
    return "Insufficient history"


def _bill_documents_for_supplier(
    session: Session,
    supplier_id: uuid.UUID,
    *,
    as_of_date: date,
) -> list[_BillDocument]:
    rows = session.execute(
        select(
            PurchaseDocument.purchase_document_id,
            PurchaseDocument.supplier_id,
            Supplier.display_name,
            PurchaseLine.transaction_date,
            PurchaseLine.item_id,
            PurchaseLine.quantity,
            PurchaseLine.line_total,
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
        .join(Supplier, Supplier.supplier_id == PurchaseDocument.supplier_id)
        .where(
            PurchaseDocument.supplier_id == supplier_id,
            *purchase_bill_conditions(
                as_of_date=as_of_date,
                positive_quantity_only=True,
            ),
            real_supplier_condition(),
        )
        .order_by(
            PurchaseLine.transaction_date,
            PurchaseDocument.purchase_document_id,
            PurchaseLine.line_sequence,
        )
    )

    documents: dict[uuid.UUID, _BillDocument] = {}
    for (
        document_id,
        row_supplier_id,
        supplier_name,
        transaction_date,
        item_id,
        quantity,
        line_total,
    ) in rows:
        document = documents.get(document_id)
        if document is None:
            document = _BillDocument(
                document_id=document_id,
                supplier_id=row_supplier_id,
                supplier_name=supplier_name,
                transaction_date=transaction_date,
                item_ids=set(),
            )
            documents[document_id] = document
        if item_id is not None:
            document.item_ids.add(item_id)
        document.line_count += 1
        document.quantity += _decimal(quantity)
        document.value += _decimal(line_total)

    return sorted(
        documents.values(),
        key=lambda document: (document.transaction_date, str(document.document_id)),
    )


def _bill_documents_for_item(
    session: Session,
    item_id: uuid.UUID,
    *,
    as_of_date: date,
) -> list[_BillDocument]:
    rows = session.execute(
        select(
            PurchaseDocument.purchase_document_id,
            PurchaseDocument.supplier_id,
            Supplier.display_name,
            PurchaseLine.transaction_date,
            PurchaseLine.item_id,
            PurchaseLine.quantity,
            PurchaseLine.line_total,
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
        .join(Supplier, Supplier.supplier_id == PurchaseDocument.supplier_id)
        .where(
            PurchaseLine.item_id == item_id,
            *purchase_bill_conditions(
                as_of_date=as_of_date,
                positive_quantity_only=True,
            ),
            real_supplier_condition(),
        )
        .order_by(
            PurchaseLine.transaction_date,
            PurchaseDocument.purchase_document_id,
            PurchaseLine.line_sequence,
        )
    )

    documents: dict[uuid.UUID, _BillDocument] = {}
    for (
        document_id,
        supplier_id,
        supplier_name,
        transaction_date,
        row_item_id,
        quantity,
        line_total,
    ) in rows:
        document = documents.get(document_id)
        if document is None:
            document = _BillDocument(
                document_id=document_id,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                transaction_date=transaction_date,
                item_ids=set(),
            )
            documents[document_id] = document
        if row_item_id is not None:
            document.item_ids.add(row_item_id)
        document.line_count += 1
        document.quantity += _decimal(quantity)
        document.value += _decimal(line_total)

    return sorted(
        documents.values(),
        key=lambda document: (document.transaction_date, str(document.document_id)),
    )


def _cluster_documents(
    documents: list[_BillDocument],
    *,
    gap_days: int,
) -> tuple[ReceivingWave, ...]:
    if gap_days < 0 or gap_days > 60:
        raise ValueError("wave gap days must be between 0 and 60")
    if not documents:
        return ()

    waves: list[ReceivingWave] = []
    current: list[_BillDocument] = [documents[0]]

    def finish(group: list[_BillDocument]) -> ReceivingWave:
        item_ids: set[uuid.UUID] = set()
        for document in group:
            item_ids.update(document.item_ids)
        return ReceivingWave(
            start_date=min(document.transaction_date for document in group),
            end_date=max(document.transaction_date for document in group),
            document_count=len(group),
            item_count=len(item_ids),
            line_count=sum(document.line_count for document in group),
            total_quantity=sum(
                (document.quantity for document in group),
                _ZERO,
            ),
            total_value=sum(
                (document.value for document in group),
                _ZERO,
            ),
        )

    for document in documents[1:]:
        latest_date = max(member.transaction_date for member in current)
        if (document.transaction_date - latest_date).days <= gap_days:
            current.append(document)
        else:
            waves.append(finish(current))
            current = [document]

    waves.append(finish(current))
    return tuple(waves)


def get_supplier_receiving_behavior(
    session: Session,
    supplier_id: uuid.UUID,
    *,
    as_of_date: date | None = None,
    wave_gap_days: int = _DEFAULT_WAVE_GAP_DAYS,
    recent_wave_limit: int = 12,
) -> SupplierReceivingBehavior:
    """Infer historical supplier receiving waves from positive purchase bills."""

    as_of = as_of_date or date.today()
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise LookupError(f"No supplier exists for {supplier_id}.")

    documents = _bill_documents_for_supplier(
        session,
        supplier_id,
        as_of_date=as_of,
    )
    waves = _cluster_documents(documents, gap_days=wave_gap_days)
    dates = [wave.start_date for wave in waves]
    (
        median_interval,
        average_interval,
        minimum_interval,
        maximum_interval,
        consistency,
    ) = _interval_summary(dates)
    confidence = _confidence(len(waves), max(0, len(waves) - 1))
    next_cycle = (
        waves[-1].end_date + timedelta(days=median_interval)
        if waves and median_interval is not None
        else None
    )

    recent_limit = max(1, min(int(recent_wave_limit), 50))
    return SupplierReceivingBehavior(
        supplier_id=supplier.supplier_id,
        supplier_name=supplier.display_name,
        as_of_date=as_of,
        wave_gap_days=wave_gap_days,
        wave_count=len(waves),
        first_wave_date=waves[0].start_date if waves else None,
        latest_wave_date=waves[-1].end_date if waves else None,
        median_interval_days=median_interval,
        average_interval_days=average_interval,
        minimum_interval_days=minimum_interval,
        maximum_interval_days=maximum_interval,
        consistency=consistency,
        confidence=confidence,
        next_observed_cycle_date=next_cycle,
        recent_waves=tuple(reversed(waves[-recent_limit:])),
    )


def _preferred_supplier(
    session: Session,
    item_id: uuid.UUID,
) -> tuple[ItemSupplier, Supplier] | None:
    return session.execute(
        select(ItemSupplier, Supplier)
        .join(Supplier, Supplier.supplier_id == ItemSupplier.supplier_id)
        .where(
            ItemSupplier.item_id == item_id,
            ItemSupplier.is_preferred == true(),
            ItemSupplier.match_status != "rejected",
        )
        .order_by(Supplier.display_name)
        .limit(1)
    ).one_or_none()


def _lead_days(
    preferred: tuple[ItemSupplier, Supplier] | None,
    fallback_supplier: Supplier | None,
    *,
    fallback_lead_days: int,
) -> tuple[int, str]:
    link = preferred[0] if preferred else None
    supplier = preferred[1] if preferred else fallback_supplier
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
        return max(0, sum(values)), (
            "preferred supplier settings"
            if preferred is not None
            else "latest bill supplier settings"
        )
    fallback = max(1, int(fallback_lead_days))
    return fallback, f"fallback {fallback} days"


def _sales_events(
    session: Session,
    item_id: uuid.UUID,
    *,
    start_date: date,
    end_date: date,
) -> dict[date, Decimal]:
    rows = session.execute(
        select(
            SalesLine.transaction_date,
            SalesLine.quantity,
        )
        .select_from(SalesLine)
        .join(
            SalesDocument,
            SalesDocument.sales_document_id == SalesLine.sales_document_id,
        )
        .where(
            SalesLine.item_id == item_id,
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.quantity > 0,
            SalesLine.transaction_date >= start_date,
            SalesLine.transaction_date <= end_date,
        )
        .order_by(SalesLine.transaction_date)
    )
    events: dict[date, Decimal] = {}
    for transaction_date, quantity in rows:
        events[transaction_date] = (
            events.get(transaction_date, _ZERO) + _decimal(quantity)
        )
    return events


def _monthly_values(
    events: dict[date, Decimal],
    *,
    start_date: date,
    months: int,
) -> list[Decimal]:
    result: list[Decimal] = []
    for offset in range(months):
        month = _shift_month(start_date, offset)
        total = sum(
            (
                quantity
                for event_date, quantity in events.items()
                if event_date.year == month.year and event_date.month == month.month
            ),
            _ZERO,
        )
        result.append(total)
    return result


def _demand_pattern(monthly_values: list[Decimal]) -> tuple[str, int]:
    positive = [value for value in monthly_values if value > 0]
    active_months = len(positive)
    if not positive:
        return "No recent invoiced demand", 0

    active_ratio = Decimal(active_months) / Decimal(len(monthly_values))
    mean = _average(monthly_values)
    if mean <= 0:
        return "No recent invoiced demand", active_months

    float_mean = float(mean)
    variance = sum(
        (float(value) - float_mean) ** 2 for value in monthly_values
    ) / len(monthly_values)
    coefficient = sqrt(variance) / float_mean if float_mean else 0.0

    if active_ratio >= Decimal("0.75") and coefficient <= 0.50:
        return "Regular", active_months
    if active_ratio >= Decimal("0.50") and coefficient <= 1.00:
        return "Variable", active_months
    if active_ratio < Decimal("0.50") and coefficient > 1.00:
        return "Lumpy", active_months
    return "Intermittent", active_months


def _current_inventory(
    session: Session,
    item_id: uuid.UUID,
) -> tuple[Decimal, Decimal]:
    row = session.execute(
        select(
            InventorySnapshotLine.on_hand,
            InventorySnapshotLine.on_order,
        )
        .select_from(InventorySnapshotLine)
        .join(
            InventorySnapshot,
            InventorySnapshot.inventory_snapshot_id
            == InventorySnapshotLine.inventory_snapshot_id,
        )
        .where(
            InventorySnapshot.is_current == true(),
            InventorySnapshotLine.item_id == item_id,
        )
        .limit(1)
    ).one_or_none()
    if row is None:
        return _ZERO, _ZERO
    return _decimal(row[0]), _decimal(row[1])


def _current_cover(
    session: Session,
    item_id: uuid.UUID,
) -> Decimal:
    return _decimal(
        session.scalar(
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
                CoverOrderLine.is_cover_order == true(),
            )
        )
    )


def get_item_replenishment_behavior(
    session: Session,
    item_id: uuid.UUID,
    *,
    as_of_date: date | None = None,
    demand_months: int = 24,
    fallback_lead_days: int = 98,
    wave_gap_days: int = _DEFAULT_WAVE_GAP_DAYS,
) -> ItemReplenishmentBehavior:
    """Return an explainable historical replenishment profile for one item."""

    as_of = as_of_date or date.today()
    demand_start, demand_end = _completed_window(as_of, demand_months)
    item = session.get(Item, item_id)
    if item is None:
        raise LookupError(f"No item exists for {item_id}.")

    all_documents = _bill_documents_for_item(
        session,
        item.item_id,
        as_of_date=as_of,
    )
    preferred = _preferred_supplier(session, item.item_id)

    supplier_source = "No purchase supplier"
    selected_supplier: Supplier | None = None
    selected_link: ItemSupplier | None = None
    if preferred is not None:
        selected_link, selected_supplier = preferred
        supplier_source = "Preferred supplier"
    elif all_documents:
        latest_document = max(
            all_documents,
            key=lambda document: (
                document.transaction_date,
                str(document.document_id),
            ),
        )
        selected_supplier = session.get(Supplier, latest_document.supplier_id)
        supplier_source = "Latest positive bill supplier"

    selected_documents = (
        [
            document
            for document in all_documents
            if selected_supplier is not None
            and document.supplier_id == selected_supplier.supplier_id
        ]
        if selected_supplier is not None
        else []
    )
    item_waves = _cluster_documents(
        selected_documents,
        gap_days=wave_gap_days,
    )
    purchase_quantities = [wave.total_quantity for wave in item_waves]
    purchase_dates = [wave.start_date for wave in item_waves]
    purchase_interval, _, _, _, purchase_consistency = _interval_summary(
        purchase_dates
    )

    supplier_behavior: SupplierReceivingBehavior | None = None
    if selected_supplier is not None:
        supplier_behavior = get_supplier_receiving_behavior(
            session,
            selected_supplier.supplier_id,
            as_of_date=as_of,
            wave_gap_days=wave_gap_days,
        )

    sales_events = _sales_events(
        session,
        item.item_id,
        start_date=demand_start,
        end_date=demand_end,
    )
    sales_quantities = list(sales_events.values())
    sales_dates = list(sales_events)
    sales_interval = _median_days(_intervals(sales_dates))
    monthly_values = _monthly_values(
        sales_events,
        start_date=demand_start,
        months=demand_months,
    )
    demand_pattern, active_sales_months = _demand_pattern(monthly_values)
    average_monthly_sales = _average(monthly_values)

    typical_purchase = _median_decimal(purchase_quantities)
    average_purchase = _average(purchase_quantities)
    typical_sale = _median_decimal(sales_quantities)
    average_sale = _average(sales_quantities)
    observed_batch_cover = (
        typical_purchase / average_monthly_sales
        if typical_purchase > 0 and average_monthly_sales > 0
        else None
    )

    lead_days, lead_source = _lead_days(
        (selected_link, selected_supplier)
        if selected_link is not None and selected_supplier is not None
        else None,
        selected_supplier,
        fallback_lead_days=fallback_lead_days,
    )
    supplier_interval = (
        supplier_behavior.median_interval_days
        if supplier_behavior is not None
        else None
    )
    observed_coverage_days = max(
        lead_days,
        supplier_interval or 0,
        purchase_interval or 0,
    )
    observed_cycle_demand = (
        average_monthly_sales
        * Decimal(observed_coverage_days)
        / _DAYS_PER_MONTH
    )

    on_hand, on_order = _current_inventory(session, item.item_id)
    projected_pool = on_hand + on_order
    explicit_cover = _current_cover(session, item.item_id)
    policy = item.replenishment_policy or "unknown"

    if policy == "make_to_order":
        behavioural_requirement = _ZERO
        behavioural_gap = _ZERO
    elif policy == "manual":
        behavioural_requirement = explicit_cover
        behavioural_gap = max(_ZERO, explicit_cover - projected_pool)
    else:
        # Explicit cover is a coverage floor, not additional forecast demand.
        behavioural_requirement = max(observed_cycle_demand, explicit_cover)
        behavioural_gap = max(_ZERO, behavioural_requirement - projected_pool)

    purchase_events = len(item_waves)
    sales_count = len(sales_events)
    supplier_waves = supplier_behavior.wave_count if supplier_behavior else 0
    if sales_count >= 8 and purchase_events >= 3 and supplier_waves >= 3:
        confidence = "High"
    elif sales_count >= 4 and purchase_events >= 2:
        confidence = "Medium"
    elif sales_count or purchase_events:
        confidence = "Low"
    else:
        confidence = "Insufficient history"

    notes: list[str] = [
        "MYOB bill dates are treated as a receiving-date proxy; they are not a confirmed physical receipt timestamp.",
        "Only active positive-quantity status B lines from ITEMPURbills.TXT are used for receiving patterns.",
        "Only invoiced positive sales are used for demand behaviour. Ordinary open sales orders remain excluded.",
        "Explicit customer cover is used as a minimum coverage floor and is not added again to forecast demand.",
        "The behavioural gap is advisory and does not yet replace the existing Suggested Order calculation.",
    ]
    if not item_waves:
        notes.append("No positive purchase-bill history was found for the selected supplier.")
    if not sales_events:
        notes.append("No positive invoiced sales were found in the selected demand window.")

    return ItemReplenishmentBehavior(
        item_id=item.item_id,
        item_number=item.item_number,
        item_name=item.item_name,
        replenishment_policy=policy,
        as_of_date=as_of,
        demand_start=demand_start,
        demand_end=demand_end,
        demand_months=demand_months,
        demand_pattern=demand_pattern,
        average_monthly_sales=average_monthly_sales,
        active_sales_months=active_sales_months,
        sales_event_count=sales_count,
        typical_sales_event_quantity=typical_sale,
        average_sales_event_quantity=average_sale,
        sales_interval_days=sales_interval,
        purchase_supplier_id=(
            selected_supplier.supplier_id if selected_supplier is not None else None
        ),
        purchase_supplier_name=(
            selected_supplier.display_name if selected_supplier is not None else None
        ),
        supplier_source=supplier_source,
        purchase_event_count=purchase_events,
        first_purchase_date=item_waves[0].start_date if item_waves else None,
        last_purchase_date=item_waves[-1].end_date if item_waves else None,
        typical_purchase_quantity=typical_purchase,
        average_purchase_quantity=average_purchase,
        purchase_interval_days=purchase_interval,
        purchase_consistency=purchase_consistency,
        supplier_wave_count=supplier_waves,
        supplier_receipt_interval_days=supplier_interval,
        supplier_receipt_consistency=(
            supplier_behavior.consistency
            if supplier_behavior is not None
            else "Insufficient history"
        ),
        last_supplier_wave_date=(
            supplier_behavior.latest_wave_date
            if supplier_behavior is not None
            else None
        ),
        next_observed_cycle_date=(
            supplier_behavior.next_observed_cycle_date
            if supplier_behavior is not None
            else None
        ),
        lead_days=lead_days,
        lead_source=lead_source,
        observed_batch_cover_months=observed_batch_cover,
        observed_coverage_days=observed_coverage_days,
        observed_cycle_demand=observed_cycle_demand,
        current_on_hand=on_hand,
        current_on_order=on_order,
        projected_pool=projected_pool,
        explicit_customer_cover=explicit_cover,
        behavioural_requirement=behavioural_requirement,
        behavioural_gap=behavioural_gap,
        confidence=confidence,
        notes=tuple(notes),
    )
