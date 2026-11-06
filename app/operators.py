"""Cabina de operadores (call center): usuarios, guion de llamada, búsqueda de cliente y registro de llamadas.

El pedido telefónico pasa por el mismo camino que el de la web (orders.prepare + cotización en el servidor):
el operador no escribe precios ni envío.
"""
import re
from typing import Any, Dict, List, Optional

from app import db, geo, orders, partners

LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,40}$")
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


def all_operators() -> List[Dict[str, Any]]:
    return [_public(r) for r in db.query("SELECT * FROM operators ORDER BY name")]


def create(login: str, name: str, password: str) -> Dict[str, Any]:
    login = (login or "").strip().lower()
    if not LOGIN_RE.match(login):
        raise ValueError("Usuario inválido (3–40: minúsculas, números, . _ -)")
    if len((name or "").strip()) < 2:
        raise ValueError("Falta el nombre del operador")
    if len(password or "") < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    if db.one("SELECT 1 FROM operators WHERE login=?", (login,)):
        raise ValueError("Ya existe ese usuario")
    db.execute("INSERT INTO operators(login, name, password_hash, created_at) VALUES (?,?,?,?)",
               (login, name.strip()[:60], partners.hash_password(password), db.now_iso()))
    return get(login)


def get(login: str) -> Optional[Dict[str, Any]]:
    return _public(db.one("SELECT * FROM operators WHERE login=?", (login,)))


def update(login: str, enabled: Optional[bool] = None, password: Optional[str] = None,
           name: Optional[str] = None) -> Dict[str, Any]:
    if not get(login):
        raise ValueError("Operador no encontrado")
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
def lookup_customer(phone: str) -> Dict[str, Any]:
    """Cliente conocido por su celular: nombre, últimas direcciones y pedidos."""
    norm = geo.normalize_phone(phone)  # lanza AddressError si no es válido
    past = orders.list_orders(phone=norm, limit=10)
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
