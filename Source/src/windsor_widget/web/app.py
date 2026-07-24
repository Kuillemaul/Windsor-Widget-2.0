"""FastAPI application factory and first read-only Windsor web pages."""

from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

from windsor_widget.config import load_settings
from windsor_widget.db.models import AppUser, Item, WebUserAccount
from windsor_widget.db.session import create_database_engine, create_session_factory
from windsor_widget.services.planning import (
    PlanningLookupError,
    get_order_analysis,
    get_planning_readiness,
)
from windsor_widget.web.auth import WebPrincipal, authenticate_user, get_principal
from windsor_widget.web.customers import build_customers_router
from windsor_widget.web.items import build_items_router
from windsor_widget.web.manufacture_orders import build_manufacture_orders_router
from windsor_widget.web.suppliers import build_suppliers_router

_WEB_ROOT = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_ROOT / "templates"))
_ALLOWED_TRENDS = {"3v3", "6v6", "yoy"}
_ALLOWED_THEMES = ("windsor", "light", "dark")


def _quantity(value: object) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value)):,.2f}"


def _integer(value: object) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def _configure_templates(environment: Environment) -> None:
    environment.filters["qty"] = _quantity
    environment.filters["integer"] = _integer


_configure_templates(_TEMPLATES.env)


def _session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return str(token)


def _validate_csrf(request: Request, supplied: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(str(expected), supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid form token.")


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/dashboard"


def _current_principal(request: Request, session: Session) -> WebPrincipal | None:
    return get_principal(session, request.session.get("user_id"))


def _require_principal(request: Request, session: Session) -> WebPrincipal:
    principal = _current_principal(request, session)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={request.url.path}"},
        )
    return principal


def _template_context(
    request: Request,
    *,
    principal: WebPrincipal | None = None,
    **values: object,
) -> dict[str, object]:
    return {
        "request": request,
        "principal": principal,
        "csrf_token": _csrf_token(request),
        "themes": _ALLOWED_THEMES,
        **values,
    }


def create_app(
    config_path: str | Path,
    *,
    secret_key: str | None = None,
) -> FastAPI:
    """Create a LAN-hosted Windsor Widget application."""

    resolved_secret = secret_key or os.environ.get("WINDSOR_WIDGET_WEB_SECRET", "")
    if len(resolved_secret) < 32:
        raise RuntimeError(
            "WINDSOR_WIDGET_WEB_SECRET must contain at least 32 characters. "
            "Use scripts\\web_workflow.ps1 to generate and load it."
        )

    settings = load_settings(Path(config_path))
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)

    app = FastAPI(
        title="Windsor Widget 2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.add_middleware(
        SessionMiddleware,
        secret_key=resolved_secret,
        session_cookie="windsor_widget_session",
        max_age=8 * 60 * 60,
        same_site="lax",
        https_only=os.environ.get("WINDSOR_WIDGET_WEB_HTTPS", "").lower()
        in {"1", "true", "yes"},
    )
    app.mount("/static", StaticFiles(directory=str(_WEB_ROOT / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.path not in {"/health"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.on_event("shutdown")
    def dispose_engine() -> None:
        engine.dispose()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "application": "Windsor Widget 2.0"}

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(
        request: Request,
        next: str = Query(default="/dashboard"),
        session: Session = Depends(_session),
    ):
        if _current_principal(request, session) is not None:
            return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="login.html",
            context=_template_context(request, next_path=_safe_next(next), error=None),
        )

    @app.post("/login", response_class=HTMLResponse)
    def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
        next_path: str = Form(default="/dashboard"),
        session: Session = Depends(_session),
    ):
        _validate_csrf(request, csrf_token)
        principal = authenticate_user(session, username=username, password=password)
        if principal is None:
            session.commit()
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="login.html",
                context=_template_context(
                    request,
                    next_path=_safe_next(next_path),
                    error="The username or password was not accepted.",
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        session.commit()
        request.session.clear()
        request.session["user_id"] = str(principal.user_id)
        request.session["csrf_token"] = secrets.token_urlsafe(32)
        return RedirectResponse(_safe_next(next_path), status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/logout")
    def logout(
        request: Request,
        csrf_token: str = Form(...),
        session: Session = Depends(_session),
    ) -> RedirectResponse:
        _require_principal(request, session)
        _validate_csrf(request, csrf_token)
        request.session.clear()
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, session: Session = Depends(_session)):
        principal = _require_principal(request, session)
        readiness = None
        readiness_error = None
        try:
            readiness = get_planning_readiness(session)
        except Exception as exc:  # dashboard should remain available during data maintenance
            readiness_error = str(exc)
        user_count = int(
            session.scalar(
                select(func.count(WebUserAccount.user_id))
                .join(AppUser, AppUser.user_id == WebUserAccount.user_id)
                .where(AppUser.is_active== true())
            )
            or 0
        )
        item_count = int(
            session.scalar(
                select(func.count(Item.item_id)).where(
                    Item.is_active== true(),
                    Item.is_inventoried== true(),
                    Item.excluded_from_item_view != true(),
                )
            )
            or 0
        )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=_template_context(
                request,
                principal=principal,
                readiness=readiness,
                readiness_error=readiness_error,
                user_count=user_count,
                item_count=item_count,
                active_page="dashboard",
            ),
        )

    @app.get("/order-analysis", response_class=HTMLResponse)
    def order_analysis(
        request: Request,
        months: int = Query(default=12, ge=1, le=120),
        lead_weeks: int = Query(default=14, ge=1, le=104),
        trend: str = Query(default="3v3"),
        limit: int = Query(default=100, ge=10, le=2000),
        q: str = Query(default=""),
        include_ok: bool = Query(default=False),
        as_of: str = Query(default=""),
        session: Session = Depends(_session),
    ):
        principal = _require_principal(request, session)
        trend_mode = trend if trend in _ALLOWED_TRENDS else "3v3"
        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError:
            as_of_date = date.today()
        requested_limit = 2000 if q.strip() else limit
        analysis = None
        error = None
        rows = ()
        try:
            analysis = get_order_analysis(
                session,
                analysis_months=months,
                fallback_lead_weeks=lead_weeks,
                trend_mode=trend_mode,
                as_of_date=as_of_date,
                limit=requested_limit,
                include_ok=include_ok,
            )
            rows = analysis.rows
            if q.strip():
                needle = q.strip().casefold()
                rows = tuple(
                    row
                    for row in rows
                    if needle in row.item_number.casefold()
                    or needle in row.item_name.casefold()
                )[:limit]
        except (PlanningLookupError, RuntimeError, ValueError) as exc:
            error = str(exc)

        return _TEMPLATES.TemplateResponse(
            request=request,
            name="order_analysis.html",
            context=_template_context(
                request,
                principal=principal,
                analysis=analysis,
                rows=rows,
                error=error,
                months=months,
                lead_weeks=lead_weeks,
                trend=trend_mode,
                limit=limit,
                query=q,
                include_ok=include_ok,
                as_of=as_of_date.isoformat(),
                active_page="order-analysis",
            ),
        )

    app.include_router(
        build_items_router(
            _session,
            _TEMPLATES,
            _require_principal,
            _template_context,
            _validate_csrf,
        )
    )

    app.include_router(
        build_customers_router(
            _session,
            _TEMPLATES,
            _require_principal,
            _template_context,
            _validate_csrf,
        )
    )

    app.include_router(
        build_suppliers_router(
            _session,
            _TEMPLATES,
            _require_principal,
            _template_context,
            _validate_csrf,
        )
    )

    app.include_router(
        build_manufacture_orders_router(
            _session,
            _TEMPLATES,
            _require_principal,
            _template_context,
            _validate_csrf,
        )
    )

    return app
