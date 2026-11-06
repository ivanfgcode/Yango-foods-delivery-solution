"""Cobro de carta.bo: solo comisión por pedido (3 %), sin plan mensual ni permanencia, y acuerdo aceptado."""
from app import billing
from tests.test_flow import ADMIN, client, order_body  # noqa: F401  (fixture)


def test_commission_only_no_plan(client):  # noqa: F811
    b = client.get("/api/wrap-roll/portal/billing", headers=ADMIN).json()
    assert b["model"] == "comision" and b["commission_pct"] == 3 and b["month_commission"] == 0
    for gone in ("price_bs", "monthly_bs", "sponsored_months", "min_paid_months", "schedule", "due_bs"):
        assert gone not in b
    assert b["state"] == "sin_aceptar"
    client.post("/api/wrap-roll/orders", json=order_body(client))
    b = client.get("/api/wrap-roll/portal/billing", headers=ADMIN).json()
    assert b["month_orders"] == 1 and b["month_sales"] == 43 and b["month_commission"] == 1.29
    assert b["months"][0]["commission"] == 1.29
    # El admin ya no edita planes.
    assert client.put("/api/admin/partners/wrap-roll/billing", headers=ADMIN, json={"price_bs": 150}).status_code in (404, 405)


def test_accept_terms_commission(client):  # noqa: F811
    bad = client.post("/api/wrap-roll/portal/billing/accept", headers=ADMIN, json={"name": "Ana Rojas", "accept": False})
    assert bad.status_code == 422
    ok = client.post("/api/wrap-roll/portal/billing/accept", headers=ADMIN, json={"name": "Ana Rojas Suárez", "accept": True}).json()
    assert ok["state"] == "activo" and ok["accepted_name"] == "Ana Rojas Suárez" and ok["terms_version"] == billing.TERMS_VERSION
    ps = client.get("/api/admin/partners", headers=ADMIN).json()
    assert ps[0]["billing"]["state"] == "activo" and ps[0]["billing"]["commission_rate"] == 0.03


def test_portal_requires_login_and_shows_commission(client):  # noqa: F811
    assert client.get("/api/wrap-roll/portal/billing").status_code == 401
    assert client.get("/api/wrap-roll/portal/summary").status_code == 401
    page = client.get("/wrap-roll/portal").text
    assert "carta.bo" in page and "3 % por pedido" in page
    assert "los paga Yango" not in page and "Bs150" not in page and "0 % de comisión" not in page


def test_landing_and_lead(client):  # noqa: F811
    page = client.get("/").text
    assert "carta.bo" in page and "3 % por pedido" in page and "sin cuotas fijas" in page and 'href="/wrap-roll"' in page
    assert "Hasta 1 km" in page and "Bs150" not in page  # modelo nuevo: comisión, sin plan fijo
    bad = client.post("/api/leads", json={"name": "A", "restaurant": "", "phone": "1"})
    assert bad.status_code == 422
    ok = client.post("/api/leads", json={"name": "Ana Rojas", "restaurant": "Pollos <b>Ana</b>", "phone": "71234567", "branches": "2"})
    assert ok.status_code == 200
    ev = [e for e in client.get("/api/admin/events", headers=ADMIN).json() if e["kind"] == "lead"][0]
    assert "Pollos bAna/b" in ev["detail"] and "71234567" in ev["detail"]


