"""Item-level sales chart and customer purchasing read models."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import CustomerAccount, Item, SalesDocument, SalesLine
from windsor_widget.services.reporting import MonthlySalesPoint, ReportingLookupError

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class SalesChartTick:
    value: Decimal
    label: str
    y: float


@dataclass(frozen=True, slots=True)
class SalesChartPoint:
    month_start: date
    label: str
    quantity: Decimal
    quantity_label: str
    trend_quantity: Decimal
    trend_label: str
    x: float
    bar_x: float
    bar_y: float
    bar_width: float
    bar_height: float
    trend_y: float
    show_label: bool


@dataclass(frozen=True, slots=True)
class SalesQuantityChart:
    width: int
    height: int
    plot_left: float
    plot_right: float
    plot_top: float
    plot_bottom: float
    zero_y: float
    trend_points: str
    points: tuple[SalesChartPoint, ...]
    ticks: tuple[SalesChartTick, ...]
    total_quantity: Decimal
    average_quantity: Decimal
    monthly_slope: Decimal
    trend_start: Decimal
    trend_end: Decimal


@dataclass(frozen=True, slots=True)
class ItemCustomerSalesRow:
    customer_account_id: uuid.UUID
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    city: str | None
    state: str | None
    period_invoice_count: int
    period_line_count: int
    period_quantity: Decimal
    period_value: Decimal
    all_time_invoice_count: int
    all_time_line_count: int
    all_time_quantity: Decimal
    all_time_value: Decimal
    first_purchase_date: date | None
    last_purchase_date: date | None


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _axis_label(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def build_monthly_sales_chart(
    monthly_sales: Iterable[MonthlySalesPoint],
    *,
    width: int = 960,
    height: int = 360,
) -> SalesQuantityChart:
    """Create a no-JavaScript SVG bar chart with a least-squares trend line."""

    values = tuple(monthly_sales)
    quantities = tuple(_decimal(point.quantity) for point in values)
    count = len(values)

    if count:
        total = sum(quantities, _ZERO)
        average = total / Decimal(count)
    else:
        total = _ZERO
        average = _ZERO

    if count >= 2:
        x_values = tuple(Decimal(index) for index in range(count))
        mean_x = sum(x_values, _ZERO) / Decimal(count)
        mean_y = average
        denominator = sum(
            ((x_value - mean_x) ** 2 for x_value in x_values),
            _ZERO,
        )
        numerator = sum(
            (
                (x_value - mean_x) * (quantity - mean_y)
                for x_value, quantity in zip(x_values, quantities, strict=True)
            ),
            _ZERO,
        )
        slope = numerator / denominator if denominator else _ZERO
        intercept = mean_y - (slope * mean_x)
        trend_values = tuple(intercept + (slope * x_value) for x_value in x_values)
    elif count == 1:
        slope = _ZERO
        trend_values = quantities
    else:
        slope = _ZERO
        trend_values = ()

    left = 68.0
    right = float(width) - 24.0
    top = 24.0
    bottom = float(height) - 54.0
    plot_width = right - left
    plot_height = bottom - top

    plotted_values = (*quantities, *trend_values, _ZERO)
    raw_min = min(plotted_values) if plotted_values else _ZERO
    raw_max = max(plotted_values) if plotted_values else Decimal("1")

    if raw_min == raw_max:
        padding = max(Decimal("1"), abs(raw_max) * Decimal("0.10"))
        plot_min = min(_ZERO, raw_min - padding)
        plot_max = max(_ZERO, raw_max + padding)
    else:
        span = raw_max - raw_min
        plot_min = raw_min - (span * Decimal("0.10")) if raw_min < 0 else _ZERO
        plot_max = raw_max + (span * Decimal("0.10")) if raw_max > 0 else _ZERO

    if plot_min == plot_max:
        plot_max = plot_min + Decimal("1")

    plot_span = plot_max - plot_min

    def y_for(value: Decimal) -> float:
        ratio = (plot_max - value) / plot_span
        return round(top + (float(ratio) * plot_height), 2)

    zero_y = y_for(_ZERO)
    tick_count = 5
    ticks: list[SalesChartTick] = []
    for index in range(tick_count):
        fraction = Decimal(index) / Decimal(tick_count - 1)
        tick_value = plot_max - (plot_span * fraction)
        ticks.append(
            SalesChartTick(
                value=tick_value,
                label=_axis_label(tick_value),
                y=y_for(tick_value),
            )
        )

    chart_points: list[SalesChartPoint] = []
    if count:
        step = plot_width / count
        bar_width = min(48.0, max(8.0, step * 0.62))
        label_every = max(1, (count + 11) // 12)
        for index, (point, quantity, trend_quantity) in enumerate(
            zip(values, quantities, trend_values, strict=True)
        ):
            x = left + (step * index) + (step / 2)
            quantity_y = y_for(quantity)
            bar_y = min(zero_y, quantity_y)
            bar_height = abs(zero_y - quantity_y)
            chart_points.append(
                SalesChartPoint(
                    month_start=point.month_start,
                    label=point.month_start.strftime("%b %y"),
                    quantity=quantity,
                    quantity_label=f"{quantity:,.2f}",
                    trend_quantity=trend_quantity,
                    trend_label=f"{trend_quantity:,.2f}",
                    x=round(x, 2),
                    bar_x=round(x - (bar_width / 2), 2),
                    bar_y=round(bar_y, 2),
                    bar_width=round(bar_width, 2),
                    bar_height=round(bar_height, 2),
                    trend_y=y_for(trend_quantity),
                    show_label=(index % label_every == 0 or index == count - 1),
                )
            )

    trend_points = " ".join(
        f"{point.x:.2f},{point.trend_y:.2f}" for point in chart_points
    )

    return SalesQuantityChart(
        width=width,
        height=height,
        plot_left=left,
        plot_right=right,
        plot_top=top,
        plot_bottom=bottom,
        zero_y=zero_y,
        trend_points=trend_points,
        points=tuple(chart_points),
        ticks=tuple(ticks),
        total_quantity=total,
        average_quantity=average,
        monthly_slope=slope,
        trend_start=trend_values[0] if trend_values else _ZERO,
        trend_end=trend_values[-1] if trend_values else _ZERO,
    )


def _sales_aggregate_statement(
    *,
    item_id: uuid.UUID,
    start_date: date | None,
    end_date: date,
):
    conditions = [
        SalesLine.item_id == item_id,
        SalesLine.is_active == true(),
        func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
        SalesLine.transaction_date <= end_date,
    ]
    if start_date is not None:
        conditions.append(SalesLine.transaction_date >= start_date)

    from_clause = (
        SalesLine.__table__
        .join(
            SalesDocument.__table__,
            SalesDocument.sales_document_id == SalesLine.sales_document_id,
        )
        .join(
            CustomerAccount.__table__,
            CustomerAccount.customer_account_id == SalesDocument.customer_account_id,
        )
    )

    return (
        select(
            CustomerAccount.customer_account_id,
            CustomerAccount.myob_record_id,
            CustomerAccount.myob_card_id,
            CustomerAccount.display_name,
            CustomerAccount.city,
            CustomerAccount.state,
            func.count(func.distinct(SalesDocument.sales_document_id)),
            func.count(SalesLine.sales_line_id),
            func.coalesce(func.sum(SalesLine.quantity), 0),
            func.coalesce(func.sum(SalesLine.line_total), 0),
            func.min(SalesLine.transaction_date),
            func.max(SalesLine.transaction_date),
        )
        .select_from(from_clause)
        .where(*conditions)
        .group_by(
            CustomerAccount.customer_account_id,
            CustomerAccount.myob_record_id,
            CustomerAccount.myob_card_id,
            CustomerAccount.display_name,
            CustomerAccount.city,
            CustomerAccount.state,
        )
    )


def get_item_customer_sales(
    session: Session,
    item_number: str,
    *,
    period_start: date,
    as_of_date: date,
    limit: int = 500,
) -> tuple[ItemCustomerSalesRow, ...]:
    """List customers who bought the item, with period and all-time net quantities."""

    if period_start > as_of_date:
        raise ValueError("period_start cannot be after as_of_date")

    item_key = item_number.strip()
    item = session.scalar(select(Item).where(Item.item_number == item_key))
    if item is None:
        raise ReportingLookupError(f"No item exists with item number {item_key!r}.")

    all_rows = session.execute(
        _sales_aggregate_statement(
            item_id=item.item_id,
            start_date=None,
            end_date=as_of_date,
        )
    ).all()
    period_rows = session.execute(
        _sales_aggregate_statement(
            item_id=item.item_id,
            start_date=period_start,
            end_date=as_of_date,
        )
    ).all()

    period_by_customer = {
        row[0]: (
            int(row[6] or 0),
            int(row[7] or 0),
            _decimal(row[8]),
            _decimal(row[9]),
        )
        for row in period_rows
    }

    result: list[ItemCustomerSalesRow] = []
    for row in all_rows:
        period_invoice_count, period_line_count, period_quantity, period_value = (
            period_by_customer.get(row[0], (0, 0, _ZERO, _ZERO))
        )
        result.append(
            ItemCustomerSalesRow(
                customer_account_id=row[0],
                myob_record_id=row[1],
                myob_card_id=row[2],
                display_name=row[3],
                city=row[4],
                state=row[5],
                period_invoice_count=period_invoice_count,
                period_line_count=period_line_count,
                period_quantity=period_quantity,
                period_value=period_value,
                all_time_invoice_count=int(row[6] or 0),
                all_time_line_count=int(row[7] or 0),
                all_time_quantity=_decimal(row[8]),
                all_time_value=_decimal(row[9]),
                first_purchase_date=row[10],
                last_purchase_date=row[11],
            )
        )

    result.sort(
        key=lambda customer: (
            -customer.period_quantity,
            -customer.all_time_quantity,
            customer.display_name.casefold(),
        )
    )
    return tuple(result[: max(1, min(int(limit), 2_000))])
