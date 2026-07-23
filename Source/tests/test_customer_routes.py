from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import windsor_widget.web.customers as customer_routes


class _Templates:
    def TemplateResponse(self, *, request, name, context, status_code=200):
        return JSONResponse(
            {
                "template": name,
                "rows": len(context.get("rows", ())),
                "items": len(context.get("item_sales", ())),
                "invoices": len(context.get("invoices", ())),
            },
            status_code=status_code,
        )


class _Session:
    def get(self, model, key):
        return SimpleNamespace(
            customer_account_id=key,
            myob_record_id="C1",
        )


def _session_dependency():
    yield _Session()


def _principal(request, session):
    return SimpleNamespace(
        user_id="00000000-0000-0000-0000-000000000001",
        can_change_operations=True,
    )


def _context(request, **values):
    return {"request": request, "csrf_token": "token", **values}


def _csrf(request, supplied):
    assert supplied == "token"


def test_customer_register_and_detail_routes(monkeypatch):
    customer_id = "00000000-0000-0000-0000-000000000010"
    monkeypatch.setattr(customer_routes, "list_customers", lambda *a, **k: (SimpleNamespace(customer_account_id=customer_id),))
    monkeypatch.setattr(customer_routes, "list_customer_states", lambda *a, **k: ("VIC",))
    monkeypatch.setattr(customer_routes, "get_customer_freight_evidence", lambda *a, **k: {})
    monkeypatch.setattr(customer_routes, "get_group_labels", lambda *a, **k: {})
    monkeypatch.setattr(
        customer_routes,
        "get_customer_summary",
        lambda *a, **k: SimpleNamespace(period_start="2026-01-01"),
    )
    monkeypatch.setattr(customer_routes, "get_customer_monthly_sales", lambda *a, **k: ())
    monkeypatch.setattr(
        customer_routes,
        "build_monthly_sales_chart",
        lambda *a, **k: SimpleNamespace(points=()),
    )
    monkeypatch.setattr(
        customer_routes,
        "get_customer_item_sales",
        lambda *a, **k: (SimpleNamespace(), SimpleNamespace()),
    )
    monkeypatch.setattr(
        customer_routes,
        "get_customer_invoices",
        lambda *a, **k: (SimpleNamespace(),),
    )
    monkeypatch.setattr(customer_routes, "get_customer_price_files", lambda *a, **k: ())

    app = FastAPI()
    app.include_router(
        customer_routes.build_customers_router(
            _session_dependency,
            _Templates(),
            _principal,
            _context,
            _csrf,
        )
    )
    client = TestClient(app)

    register = client.get("/customers?q=Customer")
    assert register.status_code == 200
    assert register.json()["template"] == "customers.html"
    assert register.json()["rows"] == 1

    detail = client.get(f"/customers/{customer_id}?months=12&as_of=2026-07-31")
    assert detail.status_code == 200
    assert detail.json() == {
        "template": "customer_detail.html",
        "rows": 0,
        "items": 2,
        "invoices": 1,
    }
