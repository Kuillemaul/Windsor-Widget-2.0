"""Browser routes for Supplier register and Supplier Summary."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from windsor_widget.services.supplier_insights import (
    get_supplier_dashboard,
    list_suppliers,
    set_supplier_default_lead_times,
    set_supplier_item_settings,
)


def _supplier_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Supplier not found.") from exc


def build_suppliers_router(
    session_dependency: Callable[..., Session],
    templates: Any,
    require_principal: Callable[[Request, Session], Any],
    template_context: Callable[..., dict[str, Any]],
    validate_csrf: Callable[[Request, str], None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/suppliers", response_class=HTMLResponse, name="suppliers")
    def suppliers_page(
        request: Request,
        q: str = Query(default=""),
        status: str = Query(default="active"),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        try:
            rows = list_suppliers(
                session,
                query=q,
                status=status,
                limit=1000,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return templates.TemplateResponse(
            request=request,
            name="suppliers.html",
            context=template_context(
                request,
                principal=principal,
                rows=rows,
                query=q,
                selected_status=status,
                active_page="suppliers",
            ),
        )

    @router.get(
        "/suppliers/{supplier_id}",
        response_class=HTMLResponse,
        name="supplier_detail",
    )
    def supplier_detail(
        request: Request,
        supplier_id: str,
        months: int = Query(default=12, ge=1, le=120),
        as_of: str = Query(default=""),
        edit: bool = Query(default=False),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        edit_mode = bool(
            edit and getattr(principal, "can_change_operations", False)
        )
        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError:
            as_of_date = date.today()

        try:
            dashboard = get_supplier_dashboard(
                session,
                _supplier_id(supplier_id),
                months=months,
                as_of_date=as_of_date,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return templates.TemplateResponse(
            request=request,
            name="supplier_detail.html",
            context=template_context(
                request,
                principal=principal,
                dashboard=dashboard,
                months=months,
                as_of=as_of_date.isoformat(),
                edit_mode=edit_mode,
                active_page="suppliers",
            ),
        )

    @router.post("/suppliers/{supplier_id}/lead-times")
    def update_supplier_lead_times(
        request: Request,
        supplier_id: str,
        manufacturing_lead_days: str = Form(default=""),
        transit_lead_days: str = Form(default=""),
        buffer_days: str = Form(default=""),
        months: int = Form(default=12),
        as_of: str = Form(default=""),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        if not bool(getattr(principal, "can_change_operations", False)):
            raise HTTPException(
                status_code=403,
                detail="Operational edit permission is required.",
            )

        resolved_id = _supplier_id(supplier_id)
        try:
            set_supplier_default_lead_times(
                session,
                supplier_id=resolved_id,
                manufacturing_lead_days=manufacturing_lead_days,
                transit_lead_days=transit_lead_days,
                buffer_days=buffer_days,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return RedirectResponse(
            f"/suppliers/{resolved_id}?months={months}&as_of={as_of}&edit=true",
            status_code=303,
        )

    @router.post("/suppliers/{supplier_id}/items/{item_id}")
    def update_supplier_item(
        request: Request,
        supplier_id: str,
        item_id: str,
        is_linked: bool = Form(default=False),
        is_preferred: bool = Form(default=False),
        supplier_item_number: str = Form(default=""),
        minimum_order_quantity: str = Form(default=""),
        manufacturing_lead_days_override: str = Form(default=""),
        transit_lead_days_override: str = Form(default=""),
        buffer_days_override: str = Form(default=""),
        months: int = Form(default=12),
        as_of: str = Form(default=""),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        if not bool(getattr(principal, "can_change_operations", False)):
            raise HTTPException(
                status_code=403,
                detail="Operational edit permission is required.",
            )

        resolved_supplier_id = _supplier_id(supplier_id)
        try:
            resolved_item_id = uuid.UUID(item_id)
            set_supplier_item_settings(
                session,
                supplier_id=resolved_supplier_id,
                item_id=resolved_item_id,
                is_linked=is_linked,
                is_preferred=is_preferred,
                supplier_item_number=supplier_item_number,
                minimum_order_quantity=minimum_order_quantity,
                manufacturing_lead_days_override=(
                    manufacturing_lead_days_override
                ),
                transit_lead_days_override=transit_lead_days_override,
                buffer_days_override=buffer_days_override,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return RedirectResponse(
            (
                f"/suppliers/{resolved_supplier_id}"
                f"?months={months}&as_of={as_of}&edit=true"
            ),
            status_code=303,
        )

    return router
