"""Browser routes for manufacture orders and the Bring In planning queue."""

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

from windsor_widget.services.manufacture_orders import (
    ConcurrentOrderChange,
    add_existing_line_to_bring_in,
    add_line_allocation,
    add_manufacture_order_line,
    cancel_bring_in_request,
    create_manufacture_order,
    delete_line_allocation,
    get_manufacture_order,
    list_bring_in_requests,
    list_customer_options,
    list_item_options,
    list_manufacture_orders,
    list_supplier_options,
    set_manufacture_order_status,
    update_manufacture_order,
    update_manufacture_order_line,
)

from windsor_widget.services.supplier_order_templates import (
    get_supplier_template_view,
    is_yuchang_supplier_name,
    open_template_on_server,
    save_supplier_template,
)
from windsor_widget.services.yu_order_export import (
    YUWorkbookChanged,
    add_yu_export_audit,
    apply_yu_item_mapping,
    export_yu_manufacture_order,
    validate_yu_order,
)


def _uuid(value: str, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}.") from exc


def _optional_uuid(value: str, *, label: str) -> uuid.UUID | None:
    if not value.strip():
        return None
    return _uuid(value, label=label)


def _date(value: str, *, label: str, required: bool = False) -> date | None:
    if not value.strip():
        if required:
            raise HTTPException(status_code=400, detail=f"{label} is required.")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}.") from exc


def _require_editor(principal: Any) -> None:
    if not bool(getattr(principal, "can_change_operations", False)):
        raise HTTPException(
            status_code=403,
            detail="Operational edit permission is required.",
        )


def _order_location(order_id: uuid.UUID, **values: object) -> str:
    query = urlencode({key: value for key, value in values.items() if value not in (None, "")})
    base = f"/manufacture-orders/{order_id}"
    return f"{base}?{query}" if query else base


def build_manufacture_orders_router(
    session_dependency: Callable[..., Session],
    templates: Any,
    require_principal: Callable[[Request, Session], Any],
    template_context: Callable[..., dict[str, Any]],
    validate_csrf: Callable[[Request, str], None],
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/manufacture-orders",
        response_class=HTMLResponse,
        name="manufacture_orders",
    )
    def manufacture_orders_page(
        request: Request,
        q: str = Query(default=""),
        supplier_id: str = Query(default=""),
        status: str = Query(default="open"),
        created: int = Query(default=0, ge=0),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        resolved_supplier = (
            _uuid(supplier_id, label="supplier") if supplier_id.strip() else None
        )
        try:
            rows = list_manufacture_orders(
                session,
                query=q,
                supplier_id=resolved_supplier,
                status=status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request=request,
            name="manufacture_orders.html",
            context=template_context(
                request,
                principal=principal,
                rows=rows,
                suppliers=list_supplier_options(session),
                query=q,
                selected_supplier=supplier_id,
                selected_status=status,
                created=created,
                active_page="manufacture-orders",
            ),
        )

    @router.get(
        "/manufacture-orders/new",
        response_class=HTMLResponse,
        name="manufacture_order_new",
    )
    def new_manufacture_order_page(
        request: Request,
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        _require_editor(principal)
        return templates.TemplateResponse(
            request=request,
            name="manufacture_order_form.html",
            context=template_context(
                request,
                principal=principal,
                suppliers=list_supplier_options(session),
                today=date.today().isoformat(),
                active_page="manufacture-orders",
            ),
        )

    @router.post("/manufacture-orders/new")
    def create_manufacture_order_route(
        request: Request,
        supplier_id: str = Form(...),
        order_number: str = Form(...),
        order_date: str = Form(...),
        expected_ready_date: str = Form(default=""),
        supplier_reference: str = Form(default=""),
        notes: str = Form(default=""),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        try:
            order = create_manufacture_order(
                session,
                supplier_id=_uuid(supplier_id, label="supplier"),
                order_number=order_number,
                order_date=_date(order_date, label="order date", required=True),
                expected_ready=_date(
                    expected_ready_date, label="expected ready date"
                ),
                supplier_reference=supplier_reference,
                notes=notes,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            _order_location(order.manufacture_order_id, created=1), status_code=303
        )

    @router.get(
        "/manufacture-orders/{order_id}",
        response_class=HTMLResponse,
        name="manufacture_order_detail",
    )
    def manufacture_order_detail_page(
        request: Request,
        order_id: str,
        created: int = Query(default=0, ge=0),
        updated: int = Query(default=0, ge=0),
        template_saved: int = Query(default=0, ge=0),
        template_opened: int = Query(default=0, ge=0),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        try:
            detail = get_manufacture_order(
                session, _uuid(order_id, label="manufacture order")
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        yu_template = None
        if is_yuchang_supplier_name(detail.supplier_name):
            yu_template = get_supplier_template_view(session, detail.supplier_id)
        return templates.TemplateResponse(
            request=request,
            name="manufacture_order_detail.html",
            context=template_context(
                request,
                principal=principal,
                order=detail,
                items=list_item_options(session),
                customers=list_customer_options(session),
                yu_template=yu_template,
                created=created,
                updated=updated,
                template_saved=template_saved,
                template_opened=template_opened,
                active_page="manufacture-orders",
            ),
        )


    @router.post("/manufacture-orders/{order_id}/yu/template")
    def save_yu_template_route(
        request: Request,
        order_id: str,
        folder_path: str = Form(...),
        file_name: str = Form(...),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            detail = get_manufacture_order(session, resolved_order)
            if not is_yuchang_supplier_name(detail.supplier_name):
                raise ValueError("This order is not for Yuchang.")
            save_supplier_template(
                session,
                supplier_id=detail.supplier_id,
                folder_path=folder_path,
                file_name=file_name,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except (FileNotFoundError, LookupError, ValueError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            _order_location(resolved_order, template_saved=1), status_code=303
        )

    @router.post("/manufacture-orders/{order_id}/yu/open-template")
    def open_yu_template_route(
        request: Request,
        order_id: str,
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            detail = get_manufacture_order(session, resolved_order)
            template = get_supplier_template_view(session, detail.supplier_id)
            if not template.full_path or not template.file_exists:
                raise FileNotFoundError("Configure an available YU workbook first.")
            open_template_on_server(template.full_path)
        except (FileNotFoundError, LookupError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            _order_location(resolved_order, template_opened=1), status_code=303
        )

    @router.get(
        "/manufacture-orders/{order_id}/yu/validate",
        response_class=HTMLResponse,
        name="yu_order_validate",
    )
    def validate_yu_order_page(
        request: Request,
        order_id: str,
        mapped: int = Query(default=0, ge=0),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            detail = get_manufacture_order(session, resolved_order)
            if not is_yuchang_supplier_name(detail.supplier_name):
                raise ValueError("This order is not for Yuchang.")
            template = get_supplier_template_view(session, detail.supplier_id)
            if not template.full_path or not template.file_exists:
                raise FileNotFoundError("Configure an available YU workbook first.")
            report = validate_yu_order(
                session,
                order_id=resolved_order,
                template_path=template.full_path,
                worksheet_name=template.worksheet_name,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request=request,
            name="yu_order_validation.html",
            context=template_context(
                request,
                principal=principal,
                order=detail,
                template=template,
                report=report,
                mapped=mapped,
                active_page="manufacture-orders",
            ),
        )

    @router.post("/manufacture-orders/{order_id}/yu/mapping")
    def apply_yu_mapping_route(
        request: Request,
        order_id: str,
        line_id: str = Form(...),
        source_row: int = Form(..., ge=1),
        workbook_mtime_ns: int = Form(...),
        clear_other_item_rows: bool = Form(default=False),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            detail = get_manufacture_order(session, resolved_order)
            template = get_supplier_template_view(session, detail.supplier_id)
            if not template.full_path or not template.file_exists:
                raise FileNotFoundError("Configure an available YU workbook first.")
            apply_yu_item_mapping(
                session,
                order_id=resolved_order,
                line_id=_uuid(line_id, label="manufacture-order line"),
                source_row=source_row,
                template_path=template.full_path,
                expected_mtime_ns=workbook_mtime_ns,
                actor_user_id=uuid.UUID(str(principal.user_id)),
                worksheet_name=template.worksheet_name,
                clear_other_item_rows=clear_other_item_rows,
            )
            session.commit()
        except YUWorkbookChanged as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (FileNotFoundError, LookupError, RuntimeError, ValueError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/manufacture-orders/{resolved_order}/yu/validate?mapped=1",
            status_code=303,
        )

    @router.post("/manufacture-orders/{order_id}/yu/export")
    def export_yu_order_route(
        request: Request,
        order_id: str,
        version: int = Form(...),
        workbook_mtime_ns: int = Form(...),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        result = None
        try:
            detail = get_manufacture_order(session, resolved_order)
            template = get_supplier_template_view(session, detail.supplier_id)
            if not template.full_path or not template.file_exists:
                raise FileNotFoundError("Configure an available YU workbook first.")
            result = export_yu_manufacture_order(
                session,
                order_id=resolved_order,
                template_path=template.full_path,
                output_directory=request.app.state.settings.folders.exports,
                expected_mtime_ns=workbook_mtime_ns,
                worksheet_name=template.worksheet_name,
            )
            set_manufacture_order_status(
                session,
                order_id=resolved_order,
                expected_version=version,
                status="in_production",
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            add_yu_export_audit(
                session,
                order_id=resolved_order,
                actor_user_id=uuid.UUID(str(principal.user_id)),
                result=result,
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            if result is not None:
                Path(result.output_path).unlink(missing_ok=True)
                Path(result.audit_path).unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except YUWorkbookChanged as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (FileNotFoundError, LookupError, RuntimeError, ValueError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(
            path=result.output_path,
            filename=Path(result.output_path).name,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

    @router.post("/manufacture-orders/{order_id}/update")
    def update_manufacture_order_route(
        request: Request,
        order_id: str,
        version: int = Form(...),
        expected_ready_date: str = Form(default=""),
        supplier_reference: str = Form(default=""),
        notes: str = Form(default=""),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_id = _uuid(order_id, label="manufacture order")
        try:
            update_manufacture_order(
                session,
                order_id=resolved_id,
                expected_version=version,
                expected_ready=_date(
                    expected_ready_date, label="expected ready date"
                ),
                supplier_reference=supplier_reference,
                notes=notes,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(_order_location(resolved_id, updated=1), status_code=303)

    @router.post("/manufacture-orders/{order_id}/status")
    def update_manufacture_order_status_route(
        request: Request,
        order_id: str,
        version: int = Form(...),
        status: str = Form(...),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_id = _uuid(order_id, label="manufacture order")
        try:
            set_manufacture_order_status(
                session,
                order_id=resolved_id,
                expected_version=version,
                status=status,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(_order_location(resolved_id, updated=1), status_code=303)

    @router.post("/manufacture-orders/{order_id}/lines")
    def add_manufacture_order_line_route(
        request: Request,
        order_id: str,
        version: int = Form(...),
        item_id: str = Form(...),
        ordered_quantity: str = Form(...),
        expected_ready_date: str = Form(default=""),
        unit_cost: str = Form(default=""),
        currency_code: str = Form(default=""),
        allocation_type: str = Form(default="general_stock"),
        allocation_quantity: str = Form(default=""),
        customer_account_id: str = Form(default=""),
        customer_reference: str = Form(default=""),
        allocation_notes: str = Form(default=""),
        add_to_bring_in: bool = Form(default=False),
        bring_in_quantity: str = Form(default=""),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_id = _uuid(order_id, label="manufacture order")
        try:
            add_manufacture_order_line(
                session,
                order_id=resolved_id,
                expected_version=version,
                item_id=_uuid(item_id, label="item"),
                ordered_quantity=ordered_quantity,
                expected_ready=_date(
                    expected_ready_date, label="expected ready date"
                ),
                unit_cost=unit_cost,
                currency_code=currency_code,
                allocation_type=allocation_type,
                allocation_quantity=allocation_quantity,
                customer_account_id=_optional_uuid(
                    customer_account_id, label="customer"
                ),
                customer_reference=customer_reference,
                allocation_notes=allocation_notes,
                add_to_bring_in=add_to_bring_in,
                bring_in_quantity=bring_in_quantity,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(_order_location(resolved_id, updated=1), status_code=303)

    @router.post("/manufacture-orders/{order_id}/lines/{line_id}/update")
    def update_manufacture_order_line_route(
        request: Request,
        order_id: str,
        line_id: str,
        version: int = Form(...),
        ordered_quantity: str = Form(...),
        cancelled_quantity: str = Form(default="0"),
        expected_ready_date: str = Form(default=""),
        readiness_override: str = Form(default="auto"),
        supplier_ready_quantity: str = Form(default=""),
        supplier_status_note: str = Form(default=""),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            update_manufacture_order_line(
                session,
                order_id=resolved_order,
                line_id=_uuid(line_id, label="manufacture-order line"),
                expected_version=version,
                ordered_quantity=ordered_quantity,
                cancelled_quantity=cancelled_quantity,
                expected_ready=_date(
                    expected_ready_date, label="expected ready date"
                ),
                readiness_override=readiness_override,
                supplier_ready_quantity=supplier_ready_quantity,
                supplier_status_note=supplier_status_note,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(_order_location(resolved_order, updated=1), status_code=303)

    @router.post("/manufacture-orders/{order_id}/lines/{line_id}/allocations")
    def add_manufacture_line_allocation_route(
        request: Request,
        order_id: str,
        line_id: str,
        version: int = Form(...),
        allocation_type: str = Form(...),
        quantity: str = Form(...),
        customer_account_id: str = Form(default=""),
        customer_reference: str = Form(default=""),
        notes: str = Form(default=""),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            add_line_allocation(
                session,
                order_id=resolved_order,
                line_id=_uuid(line_id, label="manufacture-order line"),
                expected_version=version,
                allocation_type=allocation_type,
                quantity=quantity,
                customer_account_id=_optional_uuid(
                    customer_account_id, label="customer"
                ),
                customer_reference=customer_reference,
                notes=notes,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(_order_location(resolved_order, updated=1), status_code=303)

    @router.post(
        "/manufacture-orders/{order_id}/lines/{line_id}/allocations/{allocation_id}/delete"
    )
    def delete_manufacture_line_allocation_route(
        request: Request,
        order_id: str,
        line_id: str,
        allocation_id: str,
        version: int = Form(...),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            delete_line_allocation(
                session,
                order_id=resolved_order,
                line_id=_uuid(line_id, label="manufacture-order line"),
                allocation_id=_uuid(allocation_id, label="allocation"),
                expected_version=version,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(_order_location(resolved_order, updated=1), status_code=303)

    @router.post("/manufacture-orders/{order_id}/lines/{line_id}/bring-in")
    def add_manufacture_line_to_bring_in_route(
        request: Request,
        order_id: str,
        line_id: str,
        version: int = Form(...),
        requested_quantity: str = Form(...),
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        resolved_order = _uuid(order_id, label="manufacture order")
        try:
            add_existing_line_to_bring_in(
                session,
                order_id=resolved_order,
                line_id=_uuid(line_id, label="manufacture-order line"),
                expected_version=version,
                requested_quantity=requested_quantity,
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except ConcurrentOrderChange as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(_order_location(resolved_order, updated=1), status_code=303)

    @router.get("/bring-in", response_class=HTMLResponse, name="bring_in")
    def bring_in_page(
        request: Request,
        q: str = Query(default=""),
        supplier_id: str = Query(default=""),
        status: str = Query(default="active"),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        resolved_supplier = (
            _uuid(supplier_id, label="supplier") if supplier_id.strip() else None
        )
        try:
            rows = list_bring_in_requests(
                session,
                status=status,
                supplier_id=resolved_supplier,
                query=q,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request=request,
            name="bring_in.html",
            context=template_context(
                request,
                principal=principal,
                rows=rows,
                suppliers=list_supplier_options(session),
                query=q,
                selected_supplier=supplier_id,
                selected_status=status,
                active_page="bring-in",
            ),
        )

    @router.post("/bring-in/{request_id}/cancel")
    def cancel_bring_in_request_route(
        request: Request,
        request_id: str,
        csrf_token: str = Form(...),
        session: Session = Depends(session_dependency),
    ):
        principal = require_principal(request, session)
        validate_csrf(request, csrf_token)
        _require_editor(principal)
        try:
            cancel_bring_in_request(
                session,
                request_id=_uuid(request_id, label="Bring In request"),
                actor_user_id=uuid.UUID(str(principal.user_id)),
            )
            session.commit()
        except (ValueError, LookupError) as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/bring-in", status_code=303)

    return router
