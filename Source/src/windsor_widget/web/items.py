"""Browser routes for the item policy, tags and Item Summary screens."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from windsor_widget.services.item_policy import list_item_policy_rows, set_item_policy
from windsor_widget.services.item_insights import (
    build_monthly_sales_chart,
    get_item_customer_sales,
)
from windsor_widget.services.planning import PlanningLookupError, get_item_planning_analysis
from windsor_widget.services.reporting import (
    ReportingLookupError,
    get_item_monthly_sales,
    get_item_summary,
)

_ALLOWED_TRENDS = {"3v3", "6v6", "yoy"}


def _safe_item_return(value: str) -> str | None:
    """Allow redirects only back into the local Items area."""
    if value.startswith("/items/") and not value.startswith("//"):
        return value
    return None


def build_items_router(
    session_dependency: Callable[..., Session],
    templates: Any,
    require_principal: Callable[[Request, Session], Any],
    template_context: Callable[..., dict[str, Any]],
    validate_csrf: Callable[[Request, str], None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/items", response_class=HTMLResponse)
    def item_policy_page(
        request: Request,
        q: str = Query(default=""),
        tag: str = Query(default=""),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        rows = list_item_policy_rows(session, query=q, tag=tag, limit=500)
        return templates.TemplateResponse(
            request=request,
            name="items.html",
            context=template_context(
                request,
                principal=principal,
                rows=rows,
                query=q,
                selected_tag=tag,
                active_page="items",
            ),
        )

    @router.get("/items/{item_number}", response_class=HTMLResponse, name="item_detail")
    def item_detail(
        request: Request,
        item_number: str,
        months: int = Query(default=12, ge=1, le=120),
        lead_weeks: int = Query(default=14, ge=1, le=104),
        trend: str = Query(default="3v3"),
        as_of: str = Query(default=""),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        trend_mode = trend if trend in _ALLOWED_TRENDS else "3v3"
        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError:
            as_of_date = date.today()

        try:
            summary = get_item_summary(
                session,
                item_number,
                months=months,
                as_of_date=as_of_date,
            )
            planning = get_item_planning_analysis(
                session,
                summary.item_number,
                analysis_months=months,
                fallback_lead_weeks=lead_weeks,
                trend_mode=trend_mode,
                as_of_date=as_of_date,
            )
            monthly_sales = get_item_monthly_sales(
                session,
                summary.item_number,
                months=months,
                as_of_date=as_of_date,
            )
            sales_chart = build_monthly_sales_chart(monthly_sales)
            customer_sales = get_item_customer_sales(
                session,
                summary.item_number,
                period_start=summary.period_start,
                as_of_date=as_of_date,
            )
            policy_rows = list_item_policy_rows(
                session,
                query=summary.item_number,
                limit=500,
            )
            policy_row = next(
                (row for row in policy_rows if row.item_number == summary.item_number),
                None,
            )
            if policy_row is None:
                raise ReportingLookupError(
                    f"No policy row exists for item {summary.item_number!r}."
                )
        except (ReportingLookupError, PlanningLookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return templates.TemplateResponse(
            request=request,
            name="item_detail.html",
            context=template_context(
                request,
                principal=principal,
                summary=summary,
                planning=planning,
                monthly_sales=monthly_sales,
                sales_chart=sales_chart,
                customer_sales=customer_sales,
                policy_row=policy_row,
                months=months,
                lead_weeks=lead_weeks,
                trend=trend_mode,
                as_of=as_of_date.isoformat(),
                active_page="items",
            ),
        )

    @router.post("/items/{item_id}/policy")
    def change_item_policy(
        request: Request,
        item_id: str,
        policy: str = Form(...),
        csrf_token: str = Form(...),
        q: str = Form(default=""),
        tag: str = Form(default=""),
        return_to: str = Form(default=""),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        if not bool(getattr(principal, "can_change_operations", False)):
            raise HTTPException(
                status_code=403,
                detail="This account cannot change item policies.",
            )

        try:
            set_item_policy(
                session,
                item_id=uuid.UUID(item_id),
                policy=policy,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        detail_destination = _safe_item_return(return_to)
        if detail_destination is not None:
            destination = detail_destination
        else:
            query = urlencode(
                {key: value for key, value in {"q": q, "tag": tag}.items() if value}
            )
            destination = f"/items?{query}" if query else "/items"
        return RedirectResponse(url=destination, status_code=303)

    return router
