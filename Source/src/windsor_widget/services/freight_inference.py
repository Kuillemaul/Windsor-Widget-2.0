"""Infer the customer freight payer from invoiced MYOB freight charges.

MYOB repeats the invoice freight amount on every exported line. Evidence is
therefore collapsed to one amount per invoice by taking the maximum absolute
freight amount across active invoiced lines.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from windsor_widget.db.models import AuditEvent, CustomerAccount, SalesDocument, SalesLine
from windsor_widget.services.reporting import period_start_for_months

_ZERO = Decimal("0")
_DEFAULT_THRESHOLD = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class CustomerFreightEvidence:
    customer_account_id: uuid.UUID
    display_name: str
    myob_card_id: str | None
    invoice_count: int
    charged_invoice_count: int
    zero_invoice_count: int
    charged_ratio: Decimal
    total_invoice_freight: Decimal
    suggested_payer: str
    confidence: str | None
    evidence_start: date | None
    evidence_end: date
    explanation: str


@dataclass(frozen=True, slots=True)
class FreightInferenceApplySummary:
    evidence_customers: int
    suggested_customer: int
    suggested_windsor: int
    unresolved: int
    applied_customer: int
    applied_windsor: int
    skipped_existing: int

    @property
    def applied_total(self) -> int:
        return self.applied_customer + self.applied_windsor


def _decimal(value: object) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _confidence(invoice_count: int, dominant_ratio: Decimal) -> str:
    if invoice_count >= 5 and dominant_ratio >= Decimal("0.80"):
        return "High"
    if invoice_count >= 3 and dominant_ratio >= Decimal("0.666666"):
        return "Medium"
    return "Low"


def get_customer_freight_evidence(
    session: Session,
    *,
    customer_ids: Iterable[uuid.UUID] | None = None,
    as_of_date: date | None = None,
    months: int | None = None,
    minimum_invoices: int = 1,
    charge_threshold: Decimal = _DEFAULT_THRESHOLD,
) -> dict[uuid.UUID, CustomerFreightEvidence]:
    """Return invoice-level freight evidence and a majority-based suggestion.

    One invoice equals one vote. A customer is suggested when more than half of
    invoices include freight. Windsor is suggested when more than half have
    zero freight. A 50/50 split remains Unknown. Customers below
    ``minimum_invoices`` also remain Unknown.
    """

    as_of = as_of_date or date.today()
    if months is not None and months <= 0:
        raise ValueError("months must be positive when supplied")
    if minimum_invoices <= 0:
        raise ValueError("minimum_invoices must be positive")
    threshold = abs(_decimal(charge_threshold))
    start_date = period_start_for_months(as_of, months) if months else None

    requested_ids = tuple(customer_ids) if customer_ids is not None else None
    if requested_ids is not None and not requested_ids:
        return {}

    conditions = [
        SalesLine.is_active == true(),
        func.upper(func.coalesce(SalesLine.sale_status, "")) == "I",
        SalesLine.transaction_date <= as_of,
    ]
    if start_date is not None:
        conditions.append(SalesLine.transaction_date >= start_date)
    if requested_ids is not None:
        conditions.append(SalesDocument.customer_account_id.in_(requested_ids))

    rows = session.execute(
        select(
            CustomerAccount.customer_account_id,
            CustomerAccount.display_name,
            CustomerAccount.myob_card_id,
            SalesDocument.sales_document_id,
            func.max(func.abs(func.coalesce(SalesLine.freight_amount, 0))),
        )
        .select_from(SalesDocument)
        .join(
            CustomerAccount,
            CustomerAccount.customer_account_id == SalesDocument.customer_account_id,
        )
        .join(
            SalesLine,
            SalesLine.sales_document_id == SalesDocument.sales_document_id,
        )
        .where(*conditions)
        .group_by(
            CustomerAccount.customer_account_id,
            CustomerAccount.display_name,
            CustomerAccount.myob_card_id,
            SalesDocument.sales_document_id,
        )
    ).all()

    grouped: dict[uuid.UUID, dict[str, object]] = {}
    for customer_id, display_name, card_id, _document_id, freight_amount in rows:
        entry = grouped.setdefault(
            customer_id,
            {
                "display_name": display_name,
                "myob_card_id": card_id,
                "invoice_freight": [],
            },
        )
        freight_values = entry["invoice_freight"]
        assert isinstance(freight_values, list)
        freight_values.append(abs(_decimal(freight_amount)))

    result: dict[uuid.UUID, CustomerFreightEvidence] = {}
    for customer_id, entry in grouped.items():
        invoice_freight = tuple(entry["invoice_freight"])
        invoice_count = len(invoice_freight)
        charged = sum(1 for amount in invoice_freight if amount > threshold)
        zero = invoice_count - charged
        ratio = _ZERO if invoice_count == 0 else Decimal(charged) / Decimal(invoice_count)

        if invoice_count < minimum_invoices:
            suggested = "unknown"
            confidence = None
            explanation = (
                f"Only {invoice_count} invoice(s) are available; "
                f"{minimum_invoices} are required."
            )
        elif charged > zero:
            suggested = "customer"
            confidence = _confidence(invoice_count, ratio)
            explanation = (
                f"Freight was charged on {charged} of {invoice_count} invoices "
                f"({ratio * 100:.0f}%)."
            )
        elif zero > charged:
            suggested = "windsor"
            dominant_ratio = Decimal(zero) / Decimal(invoice_count)
            confidence = _confidence(invoice_count, dominant_ratio)
            explanation = (
                f"Freight was zero on {zero} of {invoice_count} invoices "
                f"({dominant_ratio * 100:.0f}%)."
            )
        else:
            suggested = "unknown"
            confidence = None
            explanation = (
                f"Freight evidence is evenly split: {charged} charged and "
                f"{zero} zero-freight invoices."
            )

        result[customer_id] = CustomerFreightEvidence(
            customer_account_id=customer_id,
            display_name=str(entry["display_name"]),
            myob_card_id=entry["myob_card_id"],
            invoice_count=invoice_count,
            charged_invoice_count=charged,
            zero_invoice_count=zero,
            charged_ratio=ratio,
            total_invoice_freight=sum(invoice_freight, _ZERO),
            suggested_payer=suggested,
            confidence=confidence,
            evidence_start=start_date,
            evidence_end=as_of,
            explanation=explanation,
        )

    return result


def apply_customer_freight_inference(
    session: Session,
    evidence_by_customer: dict[uuid.UUID, CustomerFreightEvidence],
    *,
    actor_user_id: uuid.UUID,
) -> FreightInferenceApplySummary:
    """Apply suggestions only where the existing freight payer is Unknown.

    Manually selected Customer or Windsor values are never overwritten.
    """

    evidence_ids = set(evidence_by_customer)
    customers = {
        customer.customer_account_id: customer
        for customer in session.scalars(select(CustomerAccount))
        if customer.customer_account_id in evidence_ids
    }

    suggested_customer = 0
    suggested_windsor = 0
    unresolved = 0
    applied_customer = 0
    applied_windsor = 0
    skipped_existing = 0

    for customer_id, evidence in evidence_by_customer.items():
        if evidence.suggested_payer == "customer":
            suggested_customer += 1
        elif evidence.suggested_payer == "windsor":
            suggested_windsor += 1
        else:
            unresolved += 1
            continue

        customer = customers.get(customer_id)
        if customer is None:
            continue
        if (customer.freight_payer or "unknown") != "unknown":
            skipped_existing += 1
            continue

        before = {"freight_payer": customer.freight_payer or "unknown"}
        after = {
            "freight_payer": evidence.suggested_payer,
            "inference": {
                "invoice_count": evidence.invoice_count,
                "charged_invoice_count": evidence.charged_invoice_count,
                "zero_invoice_count": evidence.zero_invoice_count,
                "charged_ratio": str(evidence.charged_ratio),
                "total_invoice_freight": str(evidence.total_invoice_freight),
                "confidence": evidence.confidence,
                "evidence_start": (
                    evidence.evidence_start.isoformat()
                    if evidence.evidence_start is not None
                    else None
                ),
                "evidence_end": evidence.evidence_end.isoformat(),
            },
        }
        customer.freight_payer = evidence.suggested_payer
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action="customer.freight_payer.inferred",
                entity_type="customer_account",
                entity_id=str(customer.customer_account_id),
                source="inference",
                summary=(
                    f"{customer.display_name} freight payer inferred as "
                    f"{evidence.suggested_payer}: {evidence.explanation}"
                ),
                before_json=json.dumps(before, sort_keys=True),
                after_json=json.dumps(after, sort_keys=True),
            )
        )
        if evidence.suggested_payer == "customer":
            applied_customer += 1
        else:
            applied_windsor += 1

    session.flush()
    return FreightInferenceApplySummary(
        evidence_customers=len(evidence_by_customer),
        suggested_customer=suggested_customer,
        suggested_windsor=suggested_windsor,
        unresolved=unresolved,
        applied_customer=applied_customer,
        applied_windsor=applied_windsor,
        skipped_existing=skipped_existing,
    )
