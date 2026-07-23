# Combined reporting for customer groups.

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import extract, func, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    CustomerAccount,
    CustomerGroup,
    CustomerPriceFile,
    Item,
    SalesDocument,
    SalesLine,
)
from windsor_widget.services.reporting import ActivityTotals, MonthlySalesPoint, period_start_for_months
from windsor_widget.services.customer_link_admin import price_file_relative_path

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class GroupLabel:
    customer_group_id: uuid.UUID
    display_name: str


@dataclass(frozen=True, slots=True)
class GroupAccountRow:
    customer_account_id: uuid.UUID
    display_name: str
    city: str | None
    state: str | None
    freight_payer: str
    period_invoices: int
    period_quantity: Decimal
    period_value: Decimal
    all_time_invoices: int
    all_time_quantity: Decimal
    all_time_value: Decimal


@dataclass(frozen=True, slots=True)
class GroupItemRow:
    item_number: str
    item_name: str
    account_count: int
    period_quantity: Decimal
    period_value: Decimal
    all_time_quantity: Decimal
    all_time_value: Decimal
    last_date: date | None


@dataclass(frozen=True, slots=True)
class GroupInvoiceRow:
    customer_account_id: uuid.UUID
    customer_name: str
    state: str | None
    sales_document_id: uuid.UUID
    invoice_no: str
    transaction_date: date
    quantity: Decimal
    freight: Decimal
    value: Decimal


@dataclass(frozen=True, slots=True)
class GroupPriceFileRow:
    customer_price_file_id: uuid.UUID
    file_name: str
    relative_path: str
    confidence: int | None


@dataclass(frozen=True, slots=True)
class GroupDashboard:
    customer_group_id: uuid.UUID
    display_name: str
    period_start: date
    as_of_date: date
    accounts: tuple[GroupAccountRow, ...]
    sales_period: ActivityTotals
    sales_all_time: ActivityTotals
    monthly_sales: tuple[MonthlySalesPoint, ...]
    items: tuple[GroupItemRow, ...]
    invoices: tuple[GroupInvoiceRow, ...]
    price_files: tuple[GroupPriceFileRow, ...]


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _month_starts(start: date, end: date) -> tuple[date, ...]:
    values = []
    current = date(start.year, start.month, 1)
    final = date(end.year, end.month, 1)
    while current <= final:
        values.append(current)
        current = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
    return tuple(values)


def get_group_labels(
    session: Session,
    customer_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, GroupLabel]:
    if not customer_ids:
        return {}
    rows = session.execute(
        select(
            CustomerAccount.customer_account_id,
            CustomerGroup.customer_group_id,
            CustomerGroup.display_name,
        )
        .join(CustomerGroup, CustomerGroup.customer_group_id == CustomerAccount.customer_group_id)
        .where(CustomerAccount.customer_account_id.in_(customer_ids))
    )
    return {
        account_id: GroupLabel(group_id, display_name)
        for account_id, group_id, display_name in rows
    }


def _totals(session: Session, account_ids: tuple[uuid.UUID, ...], start: date | None, end: date) -> ActivityTotals:
    conditions = [
        SalesDocument.customer_account_id.in_(account_ids),
        SalesLine.is_active == true(),
        func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
        SalesLine.transaction_date <= end,
    ]
    if start is not None:
        conditions.append(SalesLine.transaction_date >= start)
    row = session.execute(
        select(
            func.count(func.distinct(SalesDocument.sales_document_id)),
            func.count(SalesLine.sales_line_id),
            func.coalesce(func.sum(SalesLine.quantity), 0),
            func.coalesce(func.sum(SalesLine.line_total), 0),
            func.min(SalesLine.transaction_date),
            func.max(SalesLine.transaction_date),
        )
        .select_from(SalesLine)
        .join(SalesDocument, SalesDocument.sales_document_id == SalesLine.sales_document_id)
        .where(*conditions)
    ).one()
    return ActivityTotals(int(row[0] or 0), int(row[1] or 0), _decimal(row[2]), _decimal(row[3]), row[4], row[5])


def _aggregate_accounts(
    session: Session,
    accounts: tuple[CustomerAccount, ...],
    start: date,
    end: date,
) -> tuple[GroupAccountRow, ...]:
    account_ids = tuple(account.customer_account_id for account in accounts)

    def aggregate(from_date: date | None):
        conditions = [
            SalesDocument.customer_account_id.in_(account_ids),
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.transaction_date <= end,
        ]
        if from_date is not None:
            conditions.append(SalesLine.transaction_date >= from_date)
        rows = session.execute(
            select(
                SalesDocument.customer_account_id,
                func.count(func.distinct(SalesDocument.sales_document_id)),
                func.coalesce(func.sum(SalesLine.quantity), 0),
                func.coalesce(func.sum(SalesLine.line_total), 0),
            )
            .select_from(SalesLine)
            .join(SalesDocument, SalesDocument.sales_document_id == SalesLine.sales_document_id)
            .where(*conditions)
            .group_by(SalesDocument.customer_account_id)
        )
        return {row[0]: (int(row[1] or 0), _decimal(row[2]), _decimal(row[3])) for row in rows}

    period = aggregate(start)
    all_time = aggregate(None)
    result = []
    for account in accounts:
        p = period.get(account.customer_account_id, (0, _ZERO, _ZERO))
        a = all_time.get(account.customer_account_id, (0, _ZERO, _ZERO))
        result.append(
            GroupAccountRow(
                account.customer_account_id,
                account.display_name,
                account.city,
                account.state,
                account.freight_payer,
                p[0],
                p[1],
                p[2],
                a[0],
                a[1],
                a[2],
            )
        )
    return tuple(result)


def _monthly(session: Session, account_ids: tuple[uuid.UUID, ...], start: date, end: date) -> tuple[MonthlySalesPoint, ...]:
    rows = session.execute(
        select(
            extract("year", SalesLine.transaction_date),
            extract("month", SalesLine.transaction_date),
            func.coalesce(func.sum(SalesLine.quantity), 0),
            func.coalesce(func.sum(SalesLine.line_total), 0),
        )
        .select_from(SalesLine)
        .join(SalesDocument, SalesDocument.sales_document_id == SalesLine.sales_document_id)
        .where(
            SalesDocument.customer_account_id.in_(account_ids),
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.transaction_date >= start,
            SalesLine.transaction_date <= end,
        )
        .group_by(extract("year", SalesLine.transaction_date), extract("month", SalesLine.transaction_date))
    )
    values = {date(int(y), int(m), 1): (_decimal(q), _decimal(v)) for y, m, q, v in rows}
    return tuple(
        MonthlySalesPoint(month, values.get(month, (_ZERO, _ZERO))[0], values.get(month, (_ZERO, _ZERO))[1])
        for month in _month_starts(start, end)
    )


def _items(session: Session, account_ids: tuple[uuid.UUID, ...], start: date, end: date) -> tuple[GroupItemRow, ...]:
    def aggregate(from_date: date | None):
        conditions = [
            SalesDocument.customer_account_id.in_(account_ids),
            SalesLine.item_id.is_not(None),
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.transaction_date <= end,
        ]
        if from_date is not None:
            conditions.append(SalesLine.transaction_date >= from_date)
        rows = session.execute(
            select(
                Item.item_id,
                Item.item_number,
                Item.item_name,
                func.count(func.distinct(SalesDocument.customer_account_id)),
                func.coalesce(func.sum(SalesLine.quantity), 0),
                func.coalesce(func.sum(SalesLine.line_total), 0),
                func.max(SalesLine.transaction_date),
            )
            .select_from(SalesLine)
            .join(SalesDocument, SalesDocument.sales_document_id == SalesLine.sales_document_id)
            .join(Item, Item.item_id == SalesLine.item_id)
            .where(*conditions)
            .group_by(Item.item_id, Item.item_number, Item.item_name)
        )
        return {r[0]: (r[1], r[2], int(r[3] or 0), _decimal(r[4]), _decimal(r[5]), r[6]) for r in rows}

    all_time = aggregate(None)
    period = aggregate(start)
    result = []
    for item_id, a in all_time.items():
        p = period.get(item_id, (a[0], a[1], 0, _ZERO, _ZERO, None))
        result.append(GroupItemRow(a[0], a[1], a[2], p[3], p[4], a[3], a[4], a[5]))
    result.sort(key=lambda row: (-row.period_quantity, -row.all_time_quantity, row.item_number.casefold()))
    return tuple(result[:2000])


def _invoices(session: Session, account_ids: tuple[uuid.UUID, ...], end: date) -> tuple[GroupInvoiceRow, ...]:
    rows = session.execute(
        select(
            CustomerAccount.customer_account_id,
            CustomerAccount.display_name,
            CustomerAccount.state,
            SalesDocument.sales_document_id,
            SalesDocument.invoice_no,
            SalesDocument.last_transaction_date,
            func.coalesce(func.sum(SalesLine.quantity), 0),
            func.max(func.abs(func.coalesce(SalesLine.freight_amount, 0))),
            func.coalesce(func.sum(SalesLine.line_total), 0),
        )
        .select_from(SalesDocument)
        .join(CustomerAccount, CustomerAccount.customer_account_id == SalesDocument.customer_account_id)
        .join(SalesLine, SalesLine.sales_document_id == SalesDocument.sales_document_id)
        .where(
            SalesDocument.customer_account_id.in_(account_ids),
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.transaction_date <= end,
        )
        .group_by(
            CustomerAccount.customer_account_id,
            CustomerAccount.display_name,
            CustomerAccount.state,
            SalesDocument.sales_document_id,
            SalesDocument.invoice_no,
            SalesDocument.last_transaction_date,
        )
        .order_by(SalesDocument.last_transaction_date.desc(), SalesDocument.invoice_no.desc())
        .limit(200)
    )
    return tuple(
        GroupInvoiceRow(r[0], r[1], r[2], r[3], r[4], r[5], _decimal(r[6]), _decimal(r[7]), _decimal(r[8]))
        for r in rows
    )


def get_group_dashboard(
    session: Session,
    group_id: uuid.UUID,
    *,
    months: int,
    as_of_date: date,
) -> GroupDashboard:
    group = session.get(CustomerGroup, group_id)
    if group is None:
        raise LookupError("Customer group not found.")

    accounts = tuple(
        session.scalars(
            select(CustomerAccount)
            .where(
                CustomerAccount.customer_group_id == group_id,
                CustomerAccount.is_active == true(),
            )
            .order_by(CustomerAccount.state, CustomerAccount.display_name)
        )
    )
    if not accounts:
        raise LookupError(f"{group.display_name} has no active accounts.")

    account_ids = tuple(account.customer_account_id for account in accounts)
    start = period_start_for_months(as_of_date, months)
    price_files = tuple(
        GroupPriceFileRow(
            p.customer_price_file_id,
            p.file_name,
            price_file_relative_path(p.file_path),
            p.confidence,
        )
        for p in session.scalars(
            select(CustomerPriceFile)
            .where(
                CustomerPriceFile.customer_group_id == group_id,
                CustomerPriceFile.is_active == true(),
            )
            .order_by(CustomerPriceFile.file_name)
        )
    )
    return GroupDashboard(
        group.customer_group_id,
        group.display_name,
        start,
        as_of_date,
        _aggregate_accounts(session, accounts, start, as_of_date),
        _totals(session, account_ids, start, as_of_date),
        _totals(session, account_ids, None, as_of_date),
        _monthly(session, account_ids, start, as_of_date),
        _items(session, account_ids, start, as_of_date),
        _invoices(session, account_ids, as_of_date),
        price_files,
    )
