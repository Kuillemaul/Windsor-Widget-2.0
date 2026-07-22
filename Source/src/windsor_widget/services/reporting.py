"""Read-only reporting services for Windsor Widget 2.0.

The UI should consume these functions instead of embedding SQL in widgets.  Every
query is scoped to committed operational tables and performs no writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable
import uuid

from sqlalchemy import extract, func, or_, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    CoverOrderDocument,
    CoverOrderLine,
    CoverOrderSnapshot,
    CustomerAccount,
    Item,
    PurchaseDocument,
    PurchaseLine,
    SalesDocument,
    SalesLine,
    Supplier,
    TransactionLineObservation,
)

_ZERO = Decimal("0")


class ReportingLookupError(LookupError):
    """Raised when an exact reporting identity cannot be resolved."""


@dataclass(frozen=True, slots=True)
class FoundationCounts:
    items: int
    customer_accounts: int
    suppliers: int
    sales_documents: int
    sales_lines: int
    cover_order_snapshots: int
    current_cover_order_snapshots: int
    cover_order_documents: int
    cover_order_lines: int
    purchase_documents: int
    purchase_lines: int
    transaction_line_observations: int


@dataclass(frozen=True, slots=True)
class ActivityTotals:
    document_count: int
    line_count: int
    quantity: Decimal
    value: Decimal
    first_date: date | None
    last_date: date | None


@dataclass(frozen=True, slots=True)
class ItemSearchResult:
    item_id: uuid.UUID
    item_number: str
    item_name: str
    is_active: bool
    excluded_from_item_view: bool


@dataclass(frozen=True, slots=True)
class CustomerSearchResult:
    customer_account_id: uuid.UUID
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    city: str | None
    state: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class MonthlySalesPoint:
    month_start: date
    quantity: Decimal
    value: Decimal


@dataclass(frozen=True, slots=True)
class ItemSummary:
    item_id: uuid.UUID
    item_number: str
    item_name: str
    description: str | None
    is_active: bool
    is_bought: bool
    is_sold: bool
    is_inventoried: bool
    excluded_from_item_view: bool
    buy_unit_measure: str | None
    sell_unit_measure: str | None
    reorder_quantity: Decimal | None
    minimum_level: Decimal | None
    standard_cost: Decimal | None
    replenishment_policy: str
    policy_source: str
    period_start: date
    as_of_date: date
    cover_snapshot_captured_at: datetime | None
    sales_all_time: ActivityTotals
    sales_period: ActivityTotals
    current_cover_orders: ActivityTotals
    purchases_all_time: ActivityTotals
    purchases_period: ActivityTotals


@dataclass(frozen=True, slots=True)
class CustomerSummary:
    customer_account_id: uuid.UUID
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    card_status: str | None
    address_line_1: str | None
    city: str | None
    state: str | None
    postcode: str | None
    contact_name: str | None
    email: str | None
    phone: str | None
    terms_description: str | None
    price_level: str | None
    shipping_method: str | None
    payment_basis: str
    freight_payer: str
    group_match_status: str
    is_active: bool
    period_start: date
    as_of_date: date
    cover_snapshot_captured_at: datetime | None
    sales_all_time: ActivityTotals
    sales_period: ActivityTotals
    current_cover_orders: ActivityTotals


def parse_iso_date(value: str) -> date:
    """Argparse-compatible ISO date parser."""

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Expected YYYY-MM-DD, received {value!r}.") from exc


def period_start_for_months(as_of_date: date, months: int) -> date:
    """Return the first day of the inclusive calendar-month window."""

    if months < 1 or months > 120:
        raise ValueError("months must be between 1 and 120")
    month_index = as_of_date.year * 12 + (as_of_date.month - 1) - (months - 1)
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _count(session: Session, column) -> int:
    return int(session.scalar(select(func.count(column))) or 0)


def get_foundation_counts(session: Session) -> FoundationCounts:
    """Return the committed master and transaction row counts used by the app."""

    current_snapshots = int(
        session.scalar(
            select(func.count(CoverOrderSnapshot.cover_order_snapshot_id)).where(
                CoverOrderSnapshot.is_current == true()
            )
        )
        or 0
    )
    return FoundationCounts(
        items=_count(session, Item.item_id),
        customer_accounts=_count(session, CustomerAccount.customer_account_id),
        suppliers=_count(session, Supplier.supplier_id),
        sales_documents=_count(session, SalesDocument.sales_document_id),
        sales_lines=_count(session, SalesLine.sales_line_id),
        cover_order_snapshots=_count(
            session, CoverOrderSnapshot.cover_order_snapshot_id
        ),
        current_cover_order_snapshots=current_snapshots,
        cover_order_documents=_count(
            session, CoverOrderDocument.cover_order_document_id
        ),
        cover_order_lines=_count(session, CoverOrderLine.cover_order_line_id),
        purchase_documents=_count(
            session, PurchaseDocument.purchase_document_id
        ),
        purchase_lines=_count(session, PurchaseLine.purchase_line_id),
        transaction_line_observations=_count(
            session,
            TransactionLineObservation.transaction_line_observation_id,
        ),
    )


def validate_foundation_counts(counts: FoundationCounts) -> tuple[str, ...]:
    """Return integrity warnings suitable for startup and support diagnostics."""

    issues: list[str] = []
    expected_observations = (
        counts.sales_lines + counts.cover_order_lines + counts.purchase_lines
    )
    if counts.transaction_line_observations < expected_observations:
        issues.append(
            "Transaction lineage count is lower than sales + cover-order + purchase lines "
            f"({counts.transaction_line_observations} < {expected_observations})."
        )
    if counts.cover_order_snapshots > 0 and counts.current_cover_order_snapshots != 1:
        issues.append(
            "Exactly one cover-order snapshot must be current when snapshots exist "
            f"(found {counts.current_cover_order_snapshots})."
        )
    return tuple(issues)


def search_items(
    session: Session,
    query: str,
    *,
    limit: int = 50,
    include_inactive: bool = False,
    include_excluded: bool = False,
) -> tuple[ItemSearchResult, ...]:
    """Search item number and item name for a UI lookup control."""

    limit = max(1, min(int(limit), 200))
    query_text = query.strip().casefold()
    statement = select(Item)
    if query_text:
        pattern = f"%{query_text}%"
        statement = statement.where(
            or_(
                func.lower(Item.item_number).like(pattern),
                func.lower(Item.normalized_name).like(pattern),
            )
        )
    if not include_inactive:
        statement = statement.where(Item.is_active == true())
    if not include_excluded:
        statement = statement.where(Item.excluded_from_item_view != true())
    statement = statement.order_by(Item.item_number).limit(limit)

    return tuple(
        ItemSearchResult(
            item_id=item.item_id,
            item_number=item.item_number,
            item_name=item.item_name,
            is_active=item.is_active,
            excluded_from_item_view=item.excluded_from_item_view,
        )
        for item in session.scalars(statement)
    )


def search_customers(
    session: Session,
    query: str,
    *,
    limit: int = 50,
    include_inactive: bool = False,
) -> tuple[CustomerSearchResult, ...]:
    """Search customer name, MYOB card ID and MYOB record ID."""

    limit = max(1, min(int(limit), 200))
    query_text = query.strip().casefold()
    statement = select(CustomerAccount)
    if query_text:
        pattern = f"%{query_text}%"
        statement = statement.where(
            or_(
                func.lower(CustomerAccount.normalized_name).like(pattern),
                func.lower(func.coalesce(CustomerAccount.myob_card_id, "")).like(
                    pattern
                ),
                func.lower(func.coalesce(CustomerAccount.myob_record_id, "")).like(
                    pattern
                ),
            )
        )
    if not include_inactive:
        statement = statement.where(CustomerAccount.is_active == true())
    statement = statement.order_by(CustomerAccount.display_name).limit(limit)

    return tuple(
        CustomerSearchResult(
            customer_account_id=customer.customer_account_id,
            myob_record_id=customer.myob_record_id,
            myob_card_id=customer.myob_card_id,
            display_name=customer.display_name,
            city=customer.city,
            state=customer.state,
            is_active=customer.is_active,
        )
        for customer in session.scalars(statement)
    )


def _item_line_totals(
    session: Session,
    line_model,
    *,
    item_id: uuid.UUID,
    line_id_column,
    document_id_column,
    active_column=None,
    start_date: date | None = None,
    end_date: date | None = None,
    joins: Iterable[tuple[object, object]] = (),
    extra_conditions: Iterable[object] = (),
) -> ActivityTotals:
    from_clause = line_model.__table__
    for target, on_clause in joins:
        target_table = getattr(target, "__table__", target)
        from_clause = from_clause.join(target_table, on_clause)

    conditions = [line_model.item_id == item_id, *extra_conditions]
    if active_column is not None:
        conditions.append(active_column == true())
    if start_date is not None:
        conditions.append(line_model.transaction_date >= start_date)
    if end_date is not None:
        conditions.append(line_model.transaction_date <= end_date)

    row = session.execute(
        select(
            func.count(func.distinct(document_id_column)),
            func.count(line_id_column),
            func.coalesce(func.sum(line_model.quantity), 0),
            func.coalesce(func.sum(line_model.line_total), 0),
            func.min(line_model.transaction_date),
            func.max(line_model.transaction_date),
        )
        .select_from(from_clause)
        .where(*conditions)
    ).one()
    return ActivityTotals(
        document_count=int(row[0] or 0),
        line_count=int(row[1] or 0),
        quantity=_decimal(row[2]),
        value=_decimal(row[3]),
        first_date=row[4],
        last_date=row[5],
    )


def _customer_sales_totals(
    session: Session,
    *,
    customer_account_id: uuid.UUID,
    start_date: date | None = None,
    end_date: date | None = None,
) -> ActivityTotals:
    conditions = [
        SalesDocument.customer_account_id == customer_account_id,
        SalesLine.is_active == true(),
        func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
    ]
    if start_date is not None:
        conditions.append(SalesLine.transaction_date >= start_date)
    if end_date is not None:
        conditions.append(SalesLine.transaction_date <= end_date)
    row = session.execute(
        select(
            func.count(func.distinct(SalesLine.sales_document_id)),
            func.count(SalesLine.sales_line_id),
            func.coalesce(func.sum(SalesLine.quantity), 0),
            func.coalesce(func.sum(SalesLine.line_total), 0),
            func.min(SalesLine.transaction_date),
            func.max(SalesLine.transaction_date),
        )
        .select_from(SalesLine)
        .join(
            SalesDocument,
            SalesDocument.sales_document_id == SalesLine.sales_document_id,
        )
        .where(*conditions)
    ).one()
    return ActivityTotals(
        document_count=int(row[0] or 0),
        line_count=int(row[1] or 0),
        quantity=_decimal(row[2]),
        value=_decimal(row[3]),
        first_date=row[4],
        last_date=row[5],
    )


def _current_cover_snapshot_captured_at(session: Session) -> datetime | None:
    return session.scalar(
        select(CoverOrderSnapshot.captured_at)
        .where(CoverOrderSnapshot.is_current == true())
        .order_by(CoverOrderSnapshot.captured_at.desc())
        .limit(1)
    )


def _customer_cover_totals(
    session: Session,
    *,
    customer_account_id: uuid.UUID,
) -> ActivityTotals:
    row = session.execute(
        select(
            func.count(func.distinct(CoverOrderLine.cover_order_document_id)),
            func.count(CoverOrderLine.cover_order_line_id),
            func.coalesce(func.sum(CoverOrderLine.quantity), 0),
            func.coalesce(func.sum(CoverOrderLine.line_total), 0),
            func.min(CoverOrderLine.transaction_date),
            func.max(CoverOrderLine.transaction_date),
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
            CoverOrderDocument.customer_account_id == customer_account_id,
            CoverOrderSnapshot.is_current == true(),
            CoverOrderLine.is_cover_order == true(),
        )
    ).one()
    return ActivityTotals(
        document_count=int(row[0] or 0),
        line_count=int(row[1] or 0),
        quantity=_decimal(row[2]),
        value=_decimal(row[3]),
        first_date=row[4],
        last_date=row[5],
    )


def get_item_summary(
    session: Session,
    item_number: str,
    *,
    months: int = 12,
    as_of_date: date | None = None,
) -> ItemSummary:
    """Return the core Item Summary read model for one exact MYOB item number."""

    as_of = as_of_date or date.today()
    period_start = period_start_for_months(as_of, months)
    key = item_number.strip()
    item = session.scalar(select(Item).where(Item.item_number == key))
    if item is None:
        raise ReportingLookupError(f"No item exists with item number {key!r}.")

    sales_all = _item_line_totals(
        session,
        SalesLine,
        item_id=item.item_id,
        line_id_column=SalesLine.sales_line_id,
        document_id_column=SalesLine.sales_document_id,
        active_column=SalesLine.is_active,
        end_date=as_of,
        extra_conditions=(
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
        ),
    )
    sales_period = _item_line_totals(
        session,
        SalesLine,
        item_id=item.item_id,
        line_id_column=SalesLine.sales_line_id,
        document_id_column=SalesLine.sales_document_id,
        active_column=SalesLine.is_active,
        start_date=period_start,
        end_date=as_of,
        extra_conditions=(
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
        ),
    )
    cover = _item_line_totals(
        session,
        CoverOrderLine,
        item_id=item.item_id,
        line_id_column=CoverOrderLine.cover_order_line_id,
        document_id_column=CoverOrderLine.cover_order_document_id,
        joins=(
            (
                CoverOrderDocument,
                CoverOrderDocument.cover_order_document_id
                == CoverOrderLine.cover_order_document_id,
            ),
            (
                CoverOrderSnapshot,
                CoverOrderSnapshot.cover_order_snapshot_id
                == CoverOrderDocument.cover_order_snapshot_id,
            ),
        ),
        extra_conditions=(
            CoverOrderSnapshot.is_current == true(),
            CoverOrderLine.is_cover_order == true(),
        ),
    )
    purchases_all = _item_line_totals(
        session,
        PurchaseLine,
        item_id=item.item_id,
        line_id_column=PurchaseLine.purchase_line_id,
        document_id_column=PurchaseLine.purchase_document_id,
        active_column=PurchaseLine.is_active,
        end_date=as_of,
    )
    purchases_period = _item_line_totals(
        session,
        PurchaseLine,
        item_id=item.item_id,
        line_id_column=PurchaseLine.purchase_line_id,
        document_id_column=PurchaseLine.purchase_document_id,
        active_column=PurchaseLine.is_active,
        start_date=period_start,
        end_date=as_of,
    )

    return ItemSummary(
        item_id=item.item_id,
        item_number=item.item_number,
        item_name=item.item_name,
        description=item.description,
        is_active=item.is_active,
        is_bought=item.is_bought,
        is_sold=item.is_sold,
        is_inventoried=item.is_inventoried,
        excluded_from_item_view=item.excluded_from_item_view,
        buy_unit_measure=item.buy_unit_measure,
        sell_unit_measure=item.sell_unit_measure,
        reorder_quantity=item.reorder_quantity,
        minimum_level=item.minimum_level,
        standard_cost=item.standard_cost,
        replenishment_policy=item.replenishment_policy,
        policy_source=item.policy_source,
        period_start=period_start,
        as_of_date=as_of,
        cover_snapshot_captured_at=_current_cover_snapshot_captured_at(session),
        sales_all_time=sales_all,
        sales_period=sales_period,
        current_cover_orders=cover,
        purchases_all_time=purchases_all,
        purchases_period=purchases_period,
    )


def get_customer_summary(
    session: Session,
    myob_record_id: str,
    *,
    months: int = 12,
    as_of_date: date | None = None,
) -> CustomerSummary:
    """Return the core Customer Summary read model for one MYOB record ID."""

    as_of = as_of_date or date.today()
    period_start = period_start_for_months(as_of, months)
    key = myob_record_id.strip()
    customer = session.scalar(
        select(CustomerAccount).where(CustomerAccount.myob_record_id == key)
    )
    if customer is None:
        raise ReportingLookupError(
            f"No customer exists with MYOB record ID {key!r}."
        )

    return CustomerSummary(
        customer_account_id=customer.customer_account_id,
        myob_record_id=customer.myob_record_id,
        myob_card_id=customer.myob_card_id,
        display_name=customer.display_name,
        card_status=customer.card_status,
        address_line_1=customer.address_line_1,
        city=customer.city,
        state=customer.state,
        postcode=customer.postcode,
        contact_name=customer.contact_name,
        email=customer.email,
        phone=customer.phone,
        terms_description=customer.terms_description,
        price_level=customer.price_level,
        shipping_method=customer.shipping_method,
        payment_basis=customer.payment_basis,
        freight_payer=customer.freight_payer,
        group_match_status=customer.group_match_status,
        is_active=customer.is_active,
        period_start=period_start,
        as_of_date=as_of,
        cover_snapshot_captured_at=_current_cover_snapshot_captured_at(session),
        sales_all_time=_customer_sales_totals(
            session,
            customer_account_id=customer.customer_account_id,
            end_date=as_of,
        ),
        sales_period=_customer_sales_totals(
            session,
            customer_account_id=customer.customer_account_id,
            start_date=period_start,
            end_date=as_of,
        ),
        current_cover_orders=_customer_cover_totals(
            session,
            customer_account_id=customer.customer_account_id,
        ),
    )


def _month_starts(start_date: date, end_date: date) -> tuple[date, ...]:
    values: list[date] = []
    cursor = start_date.replace(day=1)
    final = end_date.replace(day=1)
    while cursor <= final:
        values.append(cursor)
        next_index = cursor.year * 12 + cursor.month
        year, zero_based_month = divmod(next_index, 12)
        cursor = date(year, zero_based_month + 1, 1)
    return tuple(values)


def _monthly_sales(
    session: Session,
    *,
    start_date: date,
    end_date: date,
    conditions: Iterable[object],
    from_clause,
) -> tuple[MonthlySalesPoint, ...]:
    year_expr = extract("year", SalesLine.transaction_date)
    month_expr = extract("month", SalesLine.transaction_date)
    rows = session.execute(
        select(
            year_expr,
            month_expr,
            func.coalesce(func.sum(SalesLine.quantity), 0),
            func.coalesce(func.sum(SalesLine.line_total), 0),
        )
        .select_from(from_clause)
        .where(
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.transaction_date >= start_date,
            SalesLine.transaction_date <= end_date,
            *conditions,
        )
        .group_by(year_expr, month_expr)
        .order_by(year_expr, month_expr)
    )
    by_month = {
        date(int(year), int(month), 1): (_decimal(quantity), _decimal(value))
        for year, month, quantity, value in rows
    }
    return tuple(
        MonthlySalesPoint(
            month_start=month_start,
            quantity=by_month.get(month_start, (_ZERO, _ZERO))[0],
            value=by_month.get(month_start, (_ZERO, _ZERO))[1],
        )
        for month_start in _month_starts(start_date, end_date)
    )


def get_item_monthly_sales(
    session: Session,
    item_number: str,
    *,
    months: int = 24,
    as_of_date: date | None = None,
) -> tuple[MonthlySalesPoint, ...]:
    """Return zero-filled monthly item sales suitable for trend charts."""

    as_of = as_of_date or date.today()
    start = period_start_for_months(as_of, months)
    item = session.scalar(
        select(Item).where(Item.item_number == item_number.strip())
    )
    if item is None:
        raise ReportingLookupError(
            f"No item exists with item number {item_number.strip()!r}."
        )
    return _monthly_sales(
        session,
        start_date=start,
        end_date=as_of,
        conditions=(SalesLine.item_id == item.item_id,),
        from_clause=SalesLine,
    )


def get_customer_monthly_sales(
    session: Session,
    myob_record_id: str,
    *,
    months: int = 24,
    as_of_date: date | None = None,
) -> tuple[MonthlySalesPoint, ...]:
    """Return zero-filled monthly customer sales suitable for trend charts."""

    as_of = as_of_date or date.today()
    start = period_start_for_months(as_of, months)
    customer = session.scalar(
        select(CustomerAccount).where(
            CustomerAccount.myob_record_id == myob_record_id.strip()
        )
    )
    if customer is None:
        raise ReportingLookupError(
            f"No customer exists with MYOB record ID {myob_record_id.strip()!r}."
        )
    from_clause = SalesLine.__table__.join(
        SalesDocument.__table__,
        SalesDocument.sales_document_id == SalesLine.sales_document_id,
    )
    return _monthly_sales(
        session,
        start_date=start,
        end_date=as_of,
        conditions=(
            SalesDocument.customer_account_id == customer.customer_account_id,
        ),
        from_clause=from_clause,
    )
