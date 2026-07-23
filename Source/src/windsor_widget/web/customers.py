"""Customer register, Customer Summary, invoice and price-file routes."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from windsor_widget.db.models import CustomerAccount, CustomerPriceFile
from windsor_widget.services.customer_insights import (
    get_customer_invoice_detail,
    get_customer_invoices,
    get_customer_item_sales,
    get_customer_price_files,
    list_customer_states,
    list_customers,
    set_customer_commercial_terms,
)
from windsor_widget.services.item_insights import build_monthly_sales_chart
from windsor_widget.services.freight_inference import (
    get_customer_freight_evidence,
)
from windsor_widget.services.reporting import (
    ReportingLookupError,
    get_customer_monthly_sales,
    get_customer_summary,
)


def _customer_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Customer not found.") from exc


def _safe_customer_return(value: str) -> str | None:
    if value.startswith("/customers/") and not value.startswith("//"):
        return value
    return None


def build_customers_router(
    session_dependency: Callable[..., Session],
    templates: Any,
    require_principal: Callable[[Request, Session], Any],
    template_context: Callable[..., dict[str, Any]],
    validate_csrf: Callable[[Request, str], None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/customers", response_class=HTMLResponse)
    def customers_page(
        request: Request,
        q: str = Query(default=""),
        state: str = Query(default=""),
        payment: str = Query(default=""),
        freight: str = Query(default=""),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        rows = list_customers(
            session,
            query=q,
            state=state,
            payment_basis=payment,
            freight_payer=freight,
            limit=1_000,
        )
        freight_evidence_by_customer = get_customer_freight_evidence(
            session,
            customer_ids=tuple(row.customer_account_id for row in rows),
        )
        return templates.TemplateResponse(
            request=request,
            name="customers.html",
            context=template_context(
                request,
                principal=principal,
                rows=rows,
                freight_evidence_by_customer=freight_evidence_by_customer,
                states=list_customer_states(session),
                query=q,
                selected_state=state,
                selected_payment=payment,
                selected_freight=freight,
                active_page="customers",
            ),
        )

    @router.get(
        "/customers/{customer_id}",
        response_class=HTMLResponse,
        name="customer_detail",
    )
    def customer_detail(
        request: Request,
        customer_id: str,
        months: int = Query(default=12, ge=1, le=120),
        as_of: str = Query(default=""),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        resolved_id = _customer_id(customer_id)
        customer = session.get(CustomerAccount, resolved_id)
        if customer is None or not customer.myob_record_id:
            raise HTTPException(status_code=404, detail="Customer not found.")

        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError:
            as_of_date = date.today()

        try:
            summary = get_customer_summary(
                session,
                customer.myob_record_id,
                months=months,
                as_of_date=as_of_date,
            )
            monthly_sales = get_customer_monthly_sales(
                session,
                customer.myob_record_id,
                months=months,
                as_of_date=as_of_date,
            )
            sales_chart = build_monthly_sales_chart(monthly_sales)
            item_sales = get_customer_item_sales(
                session,
                resolved_id,
                period_start=summary.period_start,
                as_of_date=as_of_date,
            )
            invoices = get_customer_invoices(
                session,
                resolved_id,
                as_of_date=as_of_date,
                limit=100,
            )
            price_files = get_customer_price_files(session, resolved_id)
            freight_evidence = get_customer_freight_evidence(
                session,
                customer_ids=(resolved_id,),
                as_of_date=as_of_date,
            ).get(resolved_id)
        except (ReportingLookupError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return templates.TemplateResponse(
            request=request,
            name="customer_detail.html",
            context=template_context(
                request,
                principal=principal,
                customer=customer,
                summary=summary,
                monthly_sales=monthly_sales,
                sales_chart=sales_chart,
                item_sales=item_sales,
                invoices=invoices,
                price_files=price_files,
                freight_evidence=freight_evidence,
                months=months,
                as_of=as_of_date.isoformat(),
                active_page="customers",
            ),
        )

    @router.post("/customers/{customer_id}/commercial-terms")
    def update_commercial_terms(
        request: Request,
        customer_id: str,
        payment_basis: str = Form(...),
        freight_payer: str = Form(...),
        csrf_token: str = Form(...),
        return_to: str = Form(default=""),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        if not bool(getattr(principal, "can_change_operations", False)):
            raise HTTPException(
                status_code=403,
                detail="This account cannot change customer settings.",
            )

        resolved_id = _customer_id(customer_id)
        try:
            set_customer_commercial_terms(
                session,
                customer_account_id=resolved_id,
                payment_basis=payment_basis,
                freight_payer=freight_payer,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        destination = _safe_customer_return(return_to)
        if destination is None:
            destination = f"/customers/{resolved_id}"
        return RedirectResponse(destination, status_code=303)

    @router.get(
        "/customers/{customer_id}/invoices/{sales_document_id}",
        response_class=HTMLResponse,
        name="customer_invoice",
    )
    def customer_invoice(
        request: Request,
        customer_id: str,
        sales_document_id: str,
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        try:
            invoice = get_customer_invoice_detail(
                session,
                _customer_id(customer_id),
                uuid.UUID(sales_document_id),
            )
        except (ValueError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return templates.TemplateResponse(
            request=request,
            name="customer_invoice.html",
            context=template_context(
                request,
                principal=principal,
                invoice=invoice,
                active_page="customers",
            ),
        )

    @router.get(
        "/customers/{customer_id}/price-files/{price_file_id}",
        name="customer_price_file",
    )
    def customer_price_file(
        request: Request,
        customer_id: str,
        price_file_id: str,
        session: Session = Depends(session_dependency),
    ):
        require_principal(request, session)
        customer = session.get(CustomerAccount, _customer_id(customer_id))
        try:
            resolved_file_id = uuid.UUID(price_file_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Price file not found.") from exc
        price_file = session.get(CustomerPriceFile, resolved_file_id)

        if (
            customer is None
            or price_file is None
            or customer.customer_group_id is None
            or price_file.customer_group_id != customer.customer_group_id
            or not price_file.is_active
        ):
            raise HTTPException(status_code=404, detail="Price file not found.")

        path = Path(price_file.file_path)
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"The linked price file is unavailable: {price_file.file_name}",
            )
        return FileResponse(path, filename=price_file.file_name)

    return router
