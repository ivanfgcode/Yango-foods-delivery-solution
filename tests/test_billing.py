"""Plan de carta.bo: precio por sucursal, meses pagados por Yango, permanencia y acuerdo."""
from datetime import date

from app import billing
from tests.test_flow import ADMIN, client  # noqa: F401  (fixture)


def test_add_months_keeps_end_of_month():
    assert billing.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert billing.add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_plan_sponsored_then_commitment(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(billing, "today", lambda: date(2026, 11, 20))
    r = client.put("/api/admin/partners/wrap-roll/billing", headers=ADMIN, json={"start_date": "2026-10-30", "branches": 2})
    assert r.status_code == 200
    b = client.get("/api/wrap-roll/portal/billing", headers=ADMIN).json()
    assert b["price_bs"] == 150 and b["branches"] == 2 and b["monthly_bs"] == 300
    assert b["sponsored_until"] == "2027-01-30" and b["commitment_until"] == "2027-04-30"
    assert b["sponsored_value_bs"] == 900 and b["due_bs"] == 0
    assert b["state"] == "sin_aceptar"  # hasta aceptar el acuerdo
    s = b["schedule"]
    assert [x["status"] for x in s[:3]] == ["patrocinado"] * 3 and s[0]["amount"] == 0 and s[0]["list_amount"] == 300
    assert [x["committed"] for x in s[3:7]] == [True, True, True, False] and s[3]["amount"] == 300

    bad = client.post("/api/wrap-roll/portal/billing/accept", headers=ADMIN, json={"name": "Ana Rojas", "accept": False})
    assert bad.status_code == 422
    ok = client.post("/api/wrap-roll/portal/billing/accept", headers=ADMIN, json={"name": "Ana Rojas Suárez", "accept": True}).json()
    assert ok["state"] == "patrocinado" and ok["accepted_name"] == "Ana Rojas Suárez" and ok["terms_current"]

    # pasado el patrocinio, sin pagar: pendiente; pagado hasta el fin del mes 4: activo
    monkeypatch.setattr(billing, "today", lambda: date(2027, 2, 5))
    b = client.get("/api/wrap-roll/portal/billing", headers=ADMIN).json()
    assert b["state"] == "pago_pendiente" and b["due_bs"] == 300
    client.put("/api/admin/partners/wrap-roll/billing", headers=ADMIN, json={"paid_until": "2027-02-28"})
    b = client.get("/api/wrap-roll/portal/billing", headers=ADMIN).json()
    assert b["state"] == "activo" and b["schedule"][3]["status"] == "pagado"


def test_portal_requires_login_and_admin_list_has_plan(client):  # noqa: F811
    assert client.get("/api/wrap-roll/portal/billing").status_code == 401
    assert client.get("/api/wrap-roll/portal/summary").status_code == 401
    ps = client.get("/api/admin/partners", headers=ADMIN).json()
    assert ps[0]["billing"]["price_bs"] == 150 and ps[0]["billing"]["sponsored_months"] == 3
    page = client.get("/wrap-roll/portal").text
    assert "carta.bo" in page and "3 meses los paga Yango" in page


def test_landing_and_lead(client):  # noqa: F811
    page = client.get("/").text
    assert "carta.bo" in page and "Bs150" in page and "los paga Yango" in page and 'href="/wrap-roll"' in page
    bad = client.post("/api/leads", json={"name": "A", "restaurant": "", "phone": "1"})
    assert bad.status_code == 422
    ok = client.post("/api/leads", json={"name": "Ana Rojas", "restaurant": "Pollos <b>Ana</b>", "phone": "71234567", "branches": "2"})
    assert ok.status_code == 200
    ev = [e for e in client.get("/api/admin/events", headers=ADMIN).json() if e["kind"] == "lead"][0]
    assert "Pollos bAna/b" in ev["detail"] and "71234567" in ev["detail"]


def test_extend_free_months_and_free_forever(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(billing, "today", lambda: date(2027, 2, 5))
    client.put("/api/admin/partners/wrap-roll/billing", headers=ADMIN, json={"start_date": "2026-10-30", "branches": 1})
    client.post("/api/wrap-roll/portal/billing/accept", headers=ADMIN, json={"name": "Ana Rojas Suárez", "accept": True})
    b = client.get("/api/wrap-roll/portal/billing", headers=ADMIN).json()
    assert b["state"] == "pago_pendiente" and b["due_bs"] == 150
    # +2 meses gratis: el primer cobro se corre a abril
    b = client.put("/api/admin/partners/wrap-roll/billing", headers=ADMIN, json={"extend_months": 2}).json()
    assert b["sponsored_months"] == 5 and b["sponsored_until"] == "2027-03-30" and b["state"] == "patrocinado" and b["due_bs"] == 0
    assert b["commitment_until"] == "2027-06-30"
    # gratis permanente: sin cobros ni permanencia
    monkeypatch.setattr(billing, "today", lambda: date(2027, 8, 1))
    b = client.put("/api/admin/partners/wrap-roll/billing", headers=ADMIN, json={"free_forever": True, "free_note": "socio fundador"}).json()
    assert b["state"] == "cortesia" and b["due_bs"] == 0 and b["free_note"] == "socio fundador"
    assert all(s["amount"] == 0 for s in b["schedule"]) and not any(s["committed"] for s in b["schedule"])
    b = client.put("/api/admin/partners/wrap-roll/billing", headers=ADMIN, json={"free_forever": False}).json()
    assert b["state"] == "pago_pendiente" and b["due_bs"] > 0
