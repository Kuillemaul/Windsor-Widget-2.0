"""Customer Summary read models and audited commercial settings."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import (
    AuditEvent,
    CustomerAccount,
    CustomerGroup,
    CustomerPriceFile,
    Item,
    SalesDocument,
    SalesLine,
)

_ZERO = Decimal("0")
_ALLOWED_PAYMENT_BASIS = {"unknown", "prepay", "account"}
_ALLOWED_FREIGHT_PAYER = {"unknown", "customer", "windsor"}


@dataclass(frozen=True, slots=True)
class CustomerListRow:
    customer_account_id: uuid.UUID
    myob_record_id: str | None
    myob_card_id: str | None
    display_name: str
    city: str | None
    state: str | None
    card_status: str | None
    payment_basis: str
    freight_payer: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class CustomerItemSalesRow:
    item_id: uuid.UUID
    item_number: str
    item_name: str
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
    last_invoice_no: str | None
    last_purchase_quantity: Decimal
    last_unit_price: Decimal
    last_discount_percent: Decimal
    last_net_unit_price: Decimal
    last_currency_code: str | None


@dataclass(frozen=True, slots=True)
class CustomerInvoiceRow:
    sales_document_id: uuid.UUID
    invoice_no: str
    first_transaction_date: date
    last_transaction_date: date
    line_count: int
    quantity: Decimal
    value: Decimal
    freight_amount: Decimal


@dataclass(frozen=True, slots=True)
class CustomerInvoiceLine:
    item_id: uuid.UUID | None
    item_number: str | None
    item_name: str
    description: str | None
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    net_unit_price: Decimal
    line_total: Decimal
    currency_code: str | None


@dataclass(frozen=True, slots=True)
class CustomerInvoiceDetail:
    sales_document_id: uuid.UUID
    customer_account_id: uuid.UUID
    customer_name: str
    invoice_no: str
    transaction_date: date
    quantity: Decimal
    value: Decimal
    freight_amount: Decimal
    lines: tuple[CustomerInvoiceLine, ...]


@dataclass(frozen=True, slots=True)
class CustomerPriceFileRow:
    customer_price_file_id: uuid.UUID
    group_name: str
    file_name: str
    file_path: str
    match_status: str
    confidence: int | None


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def list_customers(
    session: Session,
    *,
    query: str = "",
    state: str = "",
    payment_basis: str = "",
    freight_payer: str = "",
    limit: int = 500,
) -> tuple[CustomerListRow, ...]:
    """Return a fast searchable customer register."""

    limit = max(1, min(int(limit), 2_000))
    statement = select(CustomerAccount)

    search_text = query.strip().casefold()
    if search_text:
        pattern = f"%{search_text}%"
        statement = statement.where(
            or_(
                func.lower(CustomerAccount.display_name).like(pattern),
                func.lower(CustomerAccount.normalized_name).like(pattern),
                func.lower(func.coalesce(CustomerAccount.myob_card_id, "")).like(pattern),
                func.lower(func.coalesce(CustomerAccount.myob_record_id, "")).like(pattern),
                func.lower(func.coalesce(CustomerAccount.city, "")).like(pattern),
            )
        )

    state_key = state.strip()
    if state_key:
        statement = statement.where(CustomerAccount.state == state_key)

    payment_key = payment_basis.strip().casefold()
    if payment_key in _ALLOWED_PAYMENT_BASIS:
        statement = statement.where(CustomerAccount.payment_basis == payment_key)

    freight_key = freight_payer.strip().casefold()
    if freight_key in _ALLOWED_FREIGHT_PAYER:
        statement = statement.where(CustomerAccount.freight_payer == freight_key)

    accounts = session.scalars(
        statement.order_by(CustomerAccount.display_name).limit(limit)
    )
    return tuple(
        CustomerListRow(
            customer_account_id=account.customer_account_id,
            myob_record_id=account.myob_record_id,
            myob_card_id=account.myob_card_id,
            display_name=account.display_name,
            city=account.city,
            state=account.state,
            card_status=account.card_status,
            payment_basis=account.payment_basis,
            freight_payer=account.freight_payer,
            is_active=account.is_active,
        )
        for account in accounts
    )


def list_customer_states(session: Session) -> tuple[str, ...]:
    values = session.scalars(
        select(CustomerAccount.state)
        .where(CustomerAccount.state.is_not(None), CustomerAccount.state != "")
        .distinct()
        .order_by(CustomerAccount.state)
    )
    return tuple(str(value) for value in values)


def _item_aggregate_statement(
    *,
    customer_account_id: uuid.UUID,
    start_date: date | None,
    end_date: date,
):
    conditions = [
        SalesDocument.customer_account_id == customer_account_id,
        SalesLine.is_active == true(),
        func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
        SalesLine.item_id.is_not(None),
        SalesLine.transaction_date <= end_date,
    ]
    if start_date is not None:
        conditions.append(SalesLine.transaction_date >= start_date)

    return (
        select(
            Item.item_id,
            Item.item_number,
            Item.item_name,
            func.count(func.distinct(SalesDocument.sales_document_id)),
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
        .join(Item, Item.item_id == SalesLine.item_id)
        .where(*conditions)
        .group_by(Item.item_id, Item.item_number, Item.item_name)
    )


def _latest_item_price_by_item(
    session: Session,
    *,
    customer_account_id: uuid.UUID,
    as_of_date: date,
) -> dict[
    uuid.UUID,
    tuple[str, Decimal, Decimal, Decimal, Decimal, str | None],
]:
    rows = session.execute(
        select(
            SalesLine.item_id,
            SalesDocument.invoice_no,
            SalesLine.quantity,
            SalesLine.unit_price,
            SalesLine.discount_percent,
            SalesLine.currency_code,
        )
        .select_from(SalesLine)
        .join(
            SalesDocument,
            SalesDocument.sales_document_id == SalesLine.sales_document_id,
        )
        .where(
            SalesDocument.customer_account_id == customer_account_id,
            SalesLine.item_id.is_not(None),
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.quantity > 0,
            SalesLine.transaction_date <= as_of_date,
        )
        .order_by(
            SalesLine.item_id,
            SalesLine.transaction_date.desc(),
            SalesDocument.invoice_no.desc(),
            SalesLine.line_sequence.desc(),
        )
    )

    result: dict[
        uuid.UUID,
        tuple[str, Decimal, Decimal, Decimal, Decimal, str | None],
    ] = {}
    for item_id, invoice_no, quantity, unit_price, discount_percent, currency_code in rows:
        if item_id in result:
            continue
        unit = _decimal(unit_price)
        discount = _decimal(discount_percent)
        net_unit = unit * (Decimal("1") - (discount / Decimal("100")))
        result[item_id] = (
            invoice_no,
            _decimal(quantity),
            unit,
            discount,
            net_unit,
            currency_code,
        )
    return result


def get_customer_item_sales(
    session: Session,
    customer_account_id: uuid.UUID,
    *,
    period_start: date,
    as_of_date: date,
    limit: int = 1_000,
) -> tuple[CustomerItemSalesRow, ...]:
    """Return items bought by one customer with period, lifetime and last-price data."""

    if period_start > as_of_date:
        raise ValueError("period_start cannot be after as_of_date")
    if session.get(CustomerAccount, customer_account_id) is None:
        raise LookupError(f"No customer exists for {customer_account_id}.")

    all_rows = session.execute(
        _item_aggregate_statement(
            customer_account_id=customer_account_id,
            start_date=None,
            end_date=as_of_date,
        )
    ).all()
    period_rows = session.execute(
        _item_aggregate_statement(
            customer_account_id=customer_account_id,
            start_date=period_start,
            end_date=as_of_date,
        )
    ).all()
    period_by_item = {
        row[0]: (
            int(row[3] or 0),
            int(row[4] or 0),
            _decimal(row[5]),
            _decimal(row[6]),
        )
        for row in period_rows
    }
    latest_by_item = _latest_item_price_by_item(
        session,
        customer_account_id=customer_account_id,
        as_of_date=as_of_date,
    )

    result: list[CustomerItemSalesRow] = []
    for row in all_rows:
        period_invoice_count, period_line_count, period_quantity, period_value = (
            period_by_item.get(row[0], (0, 0, _ZERO, _ZERO))
        )
        latest = latest_by_item.get(
            row[0],
            ("", _ZERO, _ZERO, _ZERO, _ZERO, None),
        )
        result.append(
            CustomerItemSalesRow(
                item_id=row[0],
                item_number=row[1],
                item_name=row[2],
                period_invoice_count=period_invoice_count,
                period_line_count=period_line_count,
                period_quantity=period_quantity,
                period_value=period_value,
                all_time_invoice_count=int(row[3] or 0),
                all_time_line_count=int(row[4] or 0),
                all_time_quantity=_decimal(row[5]),
                all_time_value=_decimal(row[6]),
                first_purchase_date=row[7],
                last_purchase_date=row[8],
                last_invoice_no=latest[0] or None,
                last_purchase_quantity=latest[1],
                last_unit_price=latest[2],
                last_discount_percent=latest[3],
                last_net_unit_price=latest[4],
                last_currency_code=latest[5],
            )
        )

    result.sort(
        key=lambda item: (
            -item.period_quantity,
            -item.all_time_quantity,
            item.item_number.casefold(),
        )
    )
    return tuple(result[: max(1, min(int(limit), 5_000))])


def get_customer_invoices(
    session: Session,
    customer_account_id: uuid.UUID,
    *,
    as_of_date: date,
    limit: int = 100,
) -> tuple[CustomerInvoiceRow, ...]:
    rows = session.execute(
        select(
            SalesDocument.sales_document_id,
            SalesDocument.invoice_no,
            SalesDocument.first_transaction_date,
            SalesDocument.last_transaction_date,
            func.count(SalesLine.sales_line_id),
            func.coalesce(func.sum(SalesLine.quantity), 0),
            func.coalesce(func.sum(SalesLine.line_total), 0),
            func.max(func.abs(func.coalesce(SalesLine.freight_amount, 0))),
        )
        .select_from(SalesDocument)
        .join(
            SalesLine,
            SalesLine.sales_document_id == SalesDocument.sales_document_id,
        )
        .where(
            SalesDocument.customer_account_id == customer_account_id,
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
            SalesLine.transaction_date <= as_of_date,
        )
        .group_by(
            SalesDocument.sales_document_id,
            SalesDocument.invoice_no,
            SalesDocument.first_transaction_date,
            SalesDocument.last_transaction_date,
        )
        .order_by(
            SalesDocument.last_transaction_date.desc(),
            SalesDocument.invoice_no.desc(),
        )
        .limit(max(1, min(int(limit), 1_000)))
    )
    return tuple(
        CustomerInvoiceRow(
            sales_document_id=row[0],
            invoice_no=row[1],
            first_transaction_date=row[2],
            last_transaction_date=row[3],
            line_count=int(row[4] or 0),
            quantity=_decimal(row[5]),
            value=_decimal(row[6]),
            freight_amount=_decimal(row[7]),
        )
        for row in rows
    )


def get_customer_invoice_detail(
    session: Session,
    customer_account_id: uuid.UUID,
    sales_document_id: uuid.UUID,
) -> CustomerInvoiceDetail:
    customer = session.get(CustomerAccount, customer_account_id)
    document = session.get(SalesDocument, sales_document_id)
    if customer is None or document is None:
        raise LookupError("The requested customer invoice does not exist.")
    if document.customer_account_id != customer_account_id:
        raise LookupError("The requested invoice does not belong to this customer.")

    rows = session.execute(
        select(SalesLine, Item)
        .outerjoin(Item, Item.item_id == SalesLine.item_id)
        .where(
            SalesLine.sales_document_id == sales_document_id,
            SalesLine.is_active == true(),
            func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
        )
        .order_by(SalesLine.line_sequence)
    ).all()

    lines: list[CustomerInvoiceLine] = []
    for line, item in rows:
        unit = _decimal(line.unit_price)
        discount = _decimal(line.discount_percent)
        lines.append(
            CustomerInvoiceLine(
                item_id=line.item_id,
                item_number=item.item_number if item is not None else line.myob_item_number,
                item_name=(
                    item.item_name
                    if item is not None
                    else (line.description or line.myob_item_number or "Unmatched item")
                ),
                description=line.description,
                quantity=_decimal(line.quantity),
                unit_price=unit,
                discount_percent=discount,
                net_unit_price=unit * (Decimal("1") - (discount / Decimal("100"))),
                line_total=_decimal(line.line_total),
                currency_code=line.currency_code,
            )
        )

    return CustomerInvoiceDetail(
        sales_document_id=document.sales_document_id,
        customer_account_id=customer.customer_account_id,
        customer_name=customer.display_name,
        invoice_no=document.invoice_no,
        transaction_date=document.last_transaction_date,
        quantity=sum((line.quantity for line in lines), _ZERO),
        value=sum((line.line_total for line in lines), _ZERO),
        freight_amount=max(
            (abs(_decimal(line.freight_amount)) for line, _item in rows),
            default=_ZERO,
        ),
        lines=tuple(lines),
    )


def get_customer_price_files(
    session: Session,
    customer_account_id: uuid.UUID,
) -> tuple[CustomerPriceFileRow, ...]:
    customer = session.get(CustomerAccount, customer_account_id)
    if customer is None or customer.customer_group_id is None:
        return ()

    rows = session.execute(
        select(CustomerPriceFile, CustomerGroup)
        .join(
            CustomerGroup,
            CustomerGroup.customer_group_id == CustomerPriceFile.customer_group_id,
        )
        .where(
            CustomerPriceFile.customer_group_id == customer.customer_group_id,
            CustomerPriceFile.is_active == true(),
        )
        .order_by(CustomerPriceFile.file_name)
    )
    return tuple(
        CustomerPriceFileRow(
            customer_price_file_id=price_file.customer_price_file_id,
            group_name=group.display_name,
            file_name=price_file.file_name,
            file_path=price_file.file_path,
            match_status=price_file.match_status,
            confidence=price_file.confidence,
        )
        for price_file, group in rows
    )


def set_customer_commercial_terms(
    session: Session,
    *,
    customer_account_id: uuid.UUID,
    payment_basis: str,
    freight_payer: str,
    actor_user_id: uuid.UUID,
) -> CustomerAccount:
    payment = payment_basis.strip().casefold()
    freight = freight_payer.strip().casefold()
    if payment not in _ALLOWED_PAYMENT_BASIS:
        raise ValueError(f"Unsupported payment basis: {payment_basis!r}")
    if freight not in _ALLOWED_FREIGHT_PAYER:
        raise ValueError(f"Unsupported freight payer: {freight_payer!r}")

    customer = session.get(CustomerAccount, customer_account_id)
    if customer is None:
        raise LookupError(f"No customer exists for {customer_account_id}.")

    before = {
        "payment_basis": customer.payment_basis,
        "freight_payer": customer.freight_payer,
    }
    after = {"payment_basis": payment, "freight_payer": freight}

    customer.payment_basis = payment
    customer.freight_payer = freight
    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action="customer.commercial_terms.updated",
            entity_type="customer_account",
            entity_id=str(customer.customer_account_id),
            source="web",
            summary=(
                f"{customer.display_name} payment basis set to {payment}; "
                f"freight payer set to {freight}."
            ),
            before_json=json.dumps(before, sort_keys=True),
            after_json=json.dumps(after, sort_keys=True),
        )
    )
    session.flush()
    return customer
