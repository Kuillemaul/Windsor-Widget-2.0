from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import windsor_widget.web.items as item_routes


class _Templates:
    def TemplateResponse(self, *, request, name, context, status_code=200):
        return JSONResponse(
            {
                "template": name,
                "item_number": getattr(context.get("summary"), "item_number", None),
                "months": context.get("months"),
                "trend": context.get("trend"),
                "customer_count": len(context.get("customer_sales", ())),
                "chart_points": len(getattr(context.get("sales_chart"), "points", ())),
            },
            status_code=status_code,
        )


def _session_dependency():
    yield object()


def _require_principal(request, session):
    return SimpleNamespace(
        user_id="00000000-0000-0000-0000-000000000001",
        can_change_operations=True,
    )


def _template_context(request, **values):
    return {"request": request, "csrf_token": "token", **values}


def _validate_csrf(request, supplied):
    assert supplied == "token"


def test_item_detail_route_assembles_sales_chart_and_customer_list(monkeypatch):
    summary = SimpleNamespace(
        item_id="00000000-0000-0000-0000-000000000010",
        item_number="I1",
        period_start="2026-02-01",
    )
    planning = SimpleNamespace(item_number="I1")
    policy = SimpleNamespace(item_number="I1")
    points = (SimpleNamespace(month_start="2026-07-01", quantity=10),)
    chart = SimpleNamespace(points=(SimpleNamespace(),))
    customers = (SimpleNamespace(display_name="Customer A"),)

    monkeypatch.setattr(item_routes, "get_item_summary", lambda *args, **kwargs: summary)
    monkeypatch.setattr(
        item_routes,
        "get_item_planning_analysis",
        lambda *args, **kwargs: planning,
    )
    monkeypatch.setattr(
        item_routes,
        "get_item_monthly_sales",
        lambda *args, **kwargs: points,
    )
    monkeypatch.setattr(
        item_routes,
        "build_monthly_sales_chart",
        lambda *args, **kwargs: chart,
    )
    monkeypatch.setattr(
        item_routes,
        "get_item_customer_sales",
        lambda *args, **kwargs: customers,
    )
    monkeypatch.setattr(
        item_routes,
        "list_item_policy_rows",
        lambda *args, **kwargs: (policy,),
    )

    app = FastAPI()
    app.include_router(
        item_routes.build_items_router(
            _session_dependency,
            _Templates(),
            _require_principal,
            _template_context,
            _validate_csrf,
        )
    )

    response = TestClient(app).get(
        "/items/I1?months=6&lead_weeks=12&trend=6v6&as_of=2026-07-22"
    )
    assert response.status_code == 200
    assert response.json() == {
        "template": "item_detail.html",
        "item_number": "I1",
        "months": 6,
        "trend": "6v6",
        "customer_count": 1,
        "chart_points": 1,
    }


def test_item_detail_return_path_rejects_external_redirects():
    assert item_routes._safe_item_return("/items/I1?months=12") == "/items/I1?months=12"
    assert item_routes._safe_item_return("https://example.com") is None
    assert item_routes._safe_item_return("//example.com/items/I1") is None
