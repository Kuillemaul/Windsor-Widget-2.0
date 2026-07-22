"""Browser routes for the item policy and tags screen."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from windsor_widget.services.item_policy import list_item_policy_rows, set_item_policy


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

    @router.post("/items/{item_id}/policy")
    def change_item_policy(
        request: Request,
        item_id: str,
        policy: str = Form(...),
        csrf_token: str = Form(...),
        q: str = Form(default=""),
        tag: str = Form(default=""),
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

        query = urlencode({key: value for key, value in {"q": q, "tag": tag}.items() if value})
        destination = f"/items?{query}" if query else "/items"
        return RedirectResponse(url=destination, status_code=303)

    return router
