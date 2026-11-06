"""Cabina de operadores (call center): usuarios, guion de llamada, búsqueda de cliente y registro de llamadas.

El pedido telefónico pasa por el mismo camino que el de la web (orders.prepare + cotización en el servidor):
el operador no escribe precios ni envío.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app import db, geo, orders, partners
from app.config import settings

LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,40}$")
ROLES = {"operador": "Operador", "supervisor": "Supervisor"}
OUTCOMES = {"pedido": "Pedido", "consulta": "Consulta", "reclamo": "Reclamo", "sin_cobertura": "Sin cobertura",
            "colgo": "Colgó / no contestó", "otro": "Otro"}

# Placeholders: {saludo} {restaurante} {operador} {cliente} {direccion} {envio} {rango} {subtotal} {total} {eta}
# {pago} {resumen} {numero}. Se reemplazan en la cabina con los datos de la llamada.
DEFAULT_SCRIPT: List[Dict[str, str]] = [
    {"id": "saludo", "title": "Saludo",
     "say": "{saludo}, gracias por llamar a {restaurante}. Le atiende {operador}. ¿Con quién tengo el gusto?",
     "tips": "Sonríe al hablar. Si no sabes a qué local llama, pregunta: «¿De qué restaurante desea pedir?»"},
    {"id": "cliente", "title": "Cliente",
     "say": "Mucho gusto, {cliente}. ¿Me confirma su número de celular, por favor?",
     "tips": "Escribe el celular: si ya pidió antes, se cargan su nombre y su última dirección."},
    {"id": "direccion", "title": "Dirección",
     "say": "¿A qué dirección le enviamos? ¿Me da una referencia: número de casa, color de la puerta o algo cercano?",
     "tips": "Mueve el mapa hasta la puerta. Si es cliente conocido: «¿Se lo enviamos a {direccion}, como la última vez?»"},
    {"id": "cobertura", "title": "Envío",
     "say": "Perfecto, sí llegamos a su zona. El envío le cuesta {envio} (en su zona va de {rango}, según distancia y demanda).",
     "tips": "Sin cobertura: «Lo siento, por ahora no llegamos a esa dirección. ¿Tiene otra dirección cercana?»"},
    {"id": "pedido", "title": "Pedido",
     "say": "¿Qué le gustaría pedir hoy?",
     "tips": "Busca el plato con el teclado. Ofrece una bebida o un postre: «¿Le agrego una gaseosa para acompañar?»"},
    {"id": "pago", "title": "Pago",
     "say": "¿Va a pagar en efectivo o con QR al mensajero? Si es efectivo, ¿con cuánto paga, para llevarle cambio?",
     "tips": "Nunca se pide tarjeta por teléfono. Solo efectivo o QR al mensajero."},
    {"id": "confirmacion", "title": "Confirmar",
     "say": "Le confirmo: {resumen}. Subtotal {subtotal}, envío {envio}, total {total}, pago {pago}. "
            "Llega en unos {eta} minutos. ¿Está todo correcto?",
     "tips": "Lee el pedido despacio. Si el cliente cambia algo, corrígelo antes de confirmar."},
    {"id": "despedida", "title": "Despedida",
     "say": "Listo, su número de pedido es {numero}. ¡Gracias por su pedido, {cliente}! Que lo disfrute.",
     "tips": "Registra el resultado de la llamada antes de atender la siguiente."},
]
SCRIPT_KEY = "call_script"


def get_script() -> List[Dict[str, str]]:
    return db.kv_get(SCRIPT_KEY) or DEFAULT_SCRIPT


def set_script(steps: Any) -> List[Dict[str, str]]:
    if steps in (None, "default"):
        db.execute("DELETE FROM kv WHERE key=?", (SCRIPT_KEY,))
        return DEFAULT_SCRIPT
    if not isinstance(steps, list) or not 1 <= len(steps) <= 15:
        raise ValueError("El guion necesita entre 1 y 15 pasos")
    clean = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise ValueError("Paso inválido")
        title = str(s.get("title") or "").strip()[:40]
        say = str(s.get("say") or "").strip()[:600]
        if not title or not say:
            raise ValueError(f"El paso {i + 1} necesita título y texto")
        sid = re.sub(r"[^a-z0-9_]", "", str(s.get("id") or "").lower())[:30] or f"paso{i + 1}"
        clean.append({"id": sid, "title": title, "say": say, "tips": str(s.get("tips") or "").strip()[:600]})
    db.kv_set(SCRIPT_KEY, clean)
    return clean


# ------------------------------------------------------------------------------------------- usuarios
def _public(row) -> Optional[Dict[str, Any]]:
    d = db.row_dict(row)
    if d:
        d.pop("password_hash", None)
        d["enabled"] = bool(d["enabled"])
    return d


ALL = "all"  # todos los equipos (admin)


def all_operators(chain_id: Any = ALL) -> List[Dict[str, Any]]:
    """chain_id: ALL = todos · None = equipo central · int = equipo de esa cadena."""
    if chain_id == ALL:
        rows = db.query("SELECT * FROM operators ORDER BY name")
    elif chain_id is None:
        rows = db.query("SELECT * FROM operators WHERE chain_id IS NULL ORDER BY name")
    else:
        rows = db.query("SELECT * FROM operators WHERE chain_id=? ORDER BY name", (int(chain_id),))
    return [_public(r) for r in rows]


def partner_ids(op: Optional[Dict[str, Any]]) -> Optional[List[int]]:
    """Restaurantes que puede atender un miembro: los de su cadena; el equipo central (o el admin), todos (None)."""
    if not op or not op.get("chain_id"):
        return None
    return [r["id"] for r in db.query("SELECT id FROM partners WHERE chain_id=?", (op["chain_id"],))]


def in_scope(op: Optional[Dict[str, Any]], partner_id: Optional[int]) -> bool:
    ids = partner_ids(op)
    return ids is None or partner_id in ids


def _clean_member(role: Optional[str], schedule: Optional[str], phone: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if role is not None:
        if role not in ROLES:
            raise ValueError("Rol inválido")
        out["role"] = role
    if schedule is not None:
        out["schedule"] = re.sub(r"[<>]", "", str(schedule)).strip()[:80] or None
    if phone is not None:
        out["phone"] = re.sub(r"[^\d+ ]", "", str(phone))[:20] or None
    return out


def create(login: str, name: str, password: str, role: str = "operador", schedule: str = "",
           phone: str = "", chain_id: Optional[int] = None) -> Dict[str, Any]:
    login = (login or "").strip().lower()
    if not LOGIN_RE.match(login):
        raise ValueError("Usuario inválido (3–40: minúsculas, números, . _ -)")
    if len((name or "").strip()) < 2:
        raise ValueError("Falta el nombre del operador")
    if len(password or "") < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    if db.one("SELECT 1 FROM operators WHERE login=?", (login,)):
        raise ValueError("Ya existe ese usuario")
    extra = _clean_member(role or "operador", schedule, phone)
    if chain_id and not db.one("SELECT 1 FROM chains WHERE id=?", (int(chain_id),)):
        raise ValueError("Cadena no encontrada")
    db.execute("INSERT INTO operators(login, name, password_hash, created_at, role, schedule, phone, chain_id) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (login, name.strip()[:60], partners.hash_password(password), db.now_iso(), extra["role"],
                extra.get("schedule"), extra.get("phone"), int(chain_id) if chain_id else None))
    db.event("equipo_alta", {"login": login, "rol": extra["role"], "cadena": chain_id})
    return get(login)


def get(login: str) -> Optional[Dict[str, Any]]:
    return _public(db.one("SELECT * FROM operators WHERE login=?", (login,)))


def update(login: str, enabled: Optional[bool] = None, password: Optional[str] = None,
           name: Optional[str] = None, role: Optional[str] = None, schedule: Optional[str] = None,
           phone: Optional[str] = None) -> Dict[str, Any]:
    if not get(login):
        raise ValueError("Operador no encontrado")
    for k, v in _clean_member(role, schedule, phone).items():
        db.execute(f"UPDATE operators SET {k}=? WHERE login=?", (v, login))
    if enabled is not None:
        db.execute("UPDATE operators SET enabled=? WHERE login=?", (1 if enabled else 0, login))
    if password:
        if len(password) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        db.execute("UPDATE operators SET password_hash=? WHERE login=?", (partners.hash_password(password), login))
    if name and len(name.strip()) >= 2:
        db.execute("UPDATE operators SET name=? WHERE login=?", (name.strip()[:60], login))
    return get(login)


def authenticate(login: str, password: str) -> Optional[Dict[str, Any]]:
    r = db.one("SELECT * FROM operators WHERE login=?", ((login or "").strip().lower(),))
    if r and r["enabled"] and partners.check_password(password or "", r["password_hash"]):
        return _public(r)
    return None


# ------------------------------------------------------------------------------------------- clientes
def lookup_customer(phone: str, partner_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Cliente conocido por su celular: nombre, últimas direcciones y pedidos (solo de los restaurantes que atiende)."""
    norm = geo.normalize_phone(phone)  # lanza AddressError si no es válido
    past = orders.list_orders(phone=norm, limit=10, partner_ids=partner_ids)
    names = {p["id"]: p["name"] for p in partners.all_partners()}
    addresses, seen = [], set()
    for o in past:
        a = o["address"]
        key = (round(a["lat"], 4), round(a["lon"], 4), a["reference"])
        if key in seen:
            continue
        seen.add(key)
        addresses.append({k: a.get(k) for k in ("lat", "lon", "text", "reference", "comment")})
    return {
        "phone": norm,
        "known": bool(past),
        "customer": past[0]["customer"] if past else None,
        "addresses": addresses[:4],
        "orders": [{"number": o["number"], "partner": names.get(o["partner_id"]), "status": o["status"],
                    "total": o["total"], "created_at": o["created_at"], "channel": o.get("channel"),
                    "items": [f"{i['quantity']}× {i['name']}" for i in o["items"]]} for o in past[:5]],
    }


# ------------------------------------------------------------------------------------------- llamadas
def start_call(operator: str, phone: str = "", partner_id: Optional[int] = None) -> Dict[str, Any]:
    cid = db.execute("INSERT INTO calls(operator, phone, partner_id, started_at) VALUES (?,?,?,?)",
                     (operator, re.sub(r"[^\d+ ]", "", phone or "")[:20], partner_id, db.now_iso()))
    return dict(db.one("SELECT * FROM calls WHERE id=?", (cid,)))


def end_call(call_id: int, operator: str, outcome: str, notes: str = "", phone: str = "",
             partner_id: Optional[int] = None, order_token: Optional[str] = None) -> Dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError("Resultado de llamada inválido")
    row = db.one("SELECT * FROM calls WHERE id=?", (call_id,))
    if not row or row["operator"] != operator:
        raise ValueError("Llamada no encontrada")
    db.execute("UPDATE calls SET outcome=?, notes=?, phone=COALESCE(NULLIF(?, ''), phone), "
               "partner_id=COALESCE(?, partner_id), order_token=COALESCE(?, order_token), ended_at=? WHERE id=?",
               (outcome, re.sub(r"[<>]", "", notes or "")[:600], re.sub(r"[^\d+ ]", "", phone or "")[:20],
                partner_id, order_token, db.now_iso(), call_id))
    return dict(db.one("SELECT * FROM calls WHERE id=?", (call_id,)))


def recent_calls(operator: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    sql, params = "SELECT * FROM calls", []
    if operator:
        sql += " WHERE operator=?"
        params.append(operator)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    names = {p["id"]: p["name"] for p in partners.all_partners()}
    out = []
    for r in db.query(sql, tuple(params)):
        d = dict(r)
        d["partner"] = names.get(d["partner_id"])
        out.append(d)
    return out


def stats(day_prefix: str) -> List[Dict[str, Any]]:
    """Llamadas y pedidos por operador para un día (AAAA-MM-DD, en UTC)."""
    rows = db.query("SELECT operator, COUNT(*) calls, SUM(outcome='pedido') orders, "
                    "ROUND(AVG((julianday(ended_at) - julianday(started_at)) * 86400)) avg_seconds "
                    "FROM calls WHERE substr(started_at, 1, 10)=? GROUP BY operator ORDER BY calls DESC",
                    (day_prefix,))
    return [dict(r) for r in rows]


# ------------------------------------------------------------------------------------------- conexiones
IDLE_MINUTES = 10      # sin latido más de esto: la conexión siguiente es otra (pausa o fin de turno)
ONLINE_SECONDS = 150   # con latido hace menos de esto: «en línea»


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def touch(login: str) -> None:
    """Latido de la cabina (al entrar y cada minuto): alarga la conexión abierta o abre otra."""
    now = _now()
    row = db.one("SELECT * FROM operator_sessions WHERE operator=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
                 (login,))
    if row and now - _dt(row["last_seen"]) <= timedelta(minutes=IDLE_MINUTES):
        db.execute("UPDATE operator_sessions SET last_seen=? WHERE id=?", (now.isoformat(), row["id"]))
        return
    if row:
        db.execute("UPDATE operator_sessions SET ended_at=last_seen WHERE id=?", (row["id"],))
    db.execute("INSERT INTO operator_sessions(operator, started_at, last_seen) VALUES (?,?,?)",
               (login, now.isoformat(), now.isoformat()))


def end_session(login: str) -> None:
    now = _now().isoformat()
    db.execute("UPDATE operator_sessions SET last_seen=?, ended_at=? WHERE operator=? AND ended_at IS NULL",
               (now, now, login))


# ------------------------------------------------------------------------------------------- estadísticas
def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def period_start(days: int) -> datetime:
    """Inicio del periodo en hora local: hoy (days=1), últimos 7, 30… días."""
    local_today = datetime.now(_tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    return (local_today - timedelta(days=max(1, days) - 1)).astimezone(timezone.utc)


def _sessions(login: str, since: datetime) -> List[Dict[str, Any]]:
    rows = db.query("SELECT started_at, last_seen, ended_at FROM operator_sessions WHERE operator=? AND last_seen>=? "
                    "ORDER BY started_at", (login, since.isoformat()))
    return [{"start": max(_dt(r["started_at"]), since), "end": _dt(r["last_seen"])} for r in rows]


def _split_by_day(sessions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Segundos conectados, primera entrada y última salida por día local."""
    tz, days = _tz(), {}
    for se in sessions:
        a, b = se["start"].astimezone(tz), se["end"].astimezone(tz)
        while a < b:
            cut = min(b, (a + timedelta(days=1)).replace(hour=0, minute=0, second=0))
            d = days.setdefault(a.date().isoformat(), {"seconds": 0, "first_in": a, "last_out": cut})
            d["seconds"] += (cut - a).total_seconds()
            d["first_in"], d["last_out"] = min(d["first_in"], a), max(d["last_out"], cut)
            a = cut
    return days


def member_stats(login: str, days: int = 1) -> Dict[str, Any]:
    since = period_start(days)
    tz = _tz()
    sess = _sessions(login, since)
    by_day = _split_by_day(sess)
    calls = [dict(r) for r in db.query("SELECT * FROM calls WHERE operator=? AND started_at>=?", (login, since.isoformat()))]
    ords = [dict(r) for r in db.query("SELECT created_at, status, total, subtotal FROM orders WHERE operator=? AND created_at>=?",
                                      (login, since.isoformat()))]
    valid = [o for o in ords if o["status"] != "cancelado"]
    durations = [(_dt(c["ended_at"]) - _dt(c["started_at"])).total_seconds() for c in calls if c["ended_at"]]
    outcomes: Dict[str, int] = defaultdict(int)
    for c in calls:
        outcomes[c["outcome"] or "en_curso"] += 1
    hours = [0] * 24
    daily: Dict[str, Dict[str, Any]] = {}
    for o in valid:
        t = _dt(o["created_at"]).astimezone(tz)
        hours[t.hour] += 1
        d = daily.setdefault(t.date().isoformat(), {"orders": 0, "sales": 0.0, "calls": 0})
        d["orders"] += 1
        d["sales"] += o["total"]
    for c in calls:
        d = daily.setdefault(_dt(c["started_at"]).astimezone(tz).date().isoformat(), {"orders": 0, "sales": 0.0, "calls": 0})
        d["calls"] += 1
    for day, v in by_day.items():
        daily.setdefault(day, {"orders": 0, "sales": 0.0, "calls": 0})
    last = db.one("SELECT last_seen, ended_at FROM operator_sessions WHERE operator=? ORDER BY id DESC LIMIT 1", (login,))
    online = bool(last and not last["ended_at"] and (_now() - _dt(last["last_seen"])).total_seconds() <= ONLINE_SECONDS)
    sales = round(sum(o["total"] for o in valid), 2)
    seconds = sum(v["seconds"] for v in by_day.values())
    first = min((v["first_in"] for v in by_day.values()), default=None)
    return {
        "login": login, "online": online, "last_seen": last["last_seen"] if last else None,
        "seconds_online": round(seconds), "hours_online": round(seconds / 3600, 2),
        "first_in": first.isoformat() if first else None,
        "calls": len(calls), "outcomes": dict(outcomes),
        "orders": len(valid), "cancelled": len(ords) - len(valid), "sales": sales,
        "avg_ticket": round(sales / len(valid), 2) if valid else 0,
        "conversion": round(len(valid) / len(calls), 3) if calls else None,
        "avg_call_seconds": round(sum(durations) / len(durations)) if durations else None,
        "orders_per_hour": round(len(valid) / (seconds / 3600), 2) if seconds >= 600 else None,
        "by_hour": hours,
        "daily": [{"day": k, "orders": v["orders"], "sales": round(v["sales"], 2), "calls": v["calls"],
                   "hours": round(by_day.get(k, {}).get("seconds", 0) / 3600, 2),
                   "first_in": by_day[k]["first_in"].strftime("%H:%M") if k in by_day else None,
                   "last_out": by_day[k]["last_out"].strftime("%H:%M") if k in by_day else None}
                  for k, v in sorted(daily.items(), reverse=True)],
    }


def team(days: int = 1, chain_id: Any = ALL) -> Dict[str, Any]:
    members = []
    chains = {r["id"]: r["name"] for r in db.query("SELECT id, name FROM chains")}
    for op in all_operators(chain_id):
        op["chain"] = chains.get(op.get("chain_id")) if op.get("chain_id") else None
        st = member_stats(op["login"], days)
        members.append({**op, "role_label": ROLES.get(op.get("role") or "operador", "Operador"), "stats": st})
    tot = {k: round(sum(m["stats"][k] for m in members), 2) for k in ("orders", "sales", "calls", "hours_online")}
    tot["online"] = sum(1 for m in members if m["stats"]["online"])
    tot["members"] = len(members)
    tot["active"] = sum(1 for m in members if m["enabled"])
    return {"days": days, "since": period_start(days).isoformat(), "members": members, "totals": tot}


def member_detail(login: str, days: int = 30) -> Dict[str, Any]:
    op = get(login)
    names = {p["id"]: p["name"] for p in partners.all_partners()}
    recent = []
    for r in db.query("SELECT token FROM orders WHERE operator=? ORDER BY created_at DESC LIMIT 20", (login,)):
        o = orders.get(r["token"])
        recent.append({"number": o["number"], "partner": names.get(o["partner_id"]), "total": o["total"],
                       "status": o["status"], "created_at": o["created_at"],
                       "customer": f"{o['customer']['first_name']} {o['customer']['last_name']}"})
    return {**op, "role_label": ROLES.get(op.get("role") or "operador"), "stats": member_stats(login, days),
            "orders": recent, "calls": recent_calls(login, limit=30)}
