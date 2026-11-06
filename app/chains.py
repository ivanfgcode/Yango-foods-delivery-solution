"""Cadenas de restaurantes: una cuenta cliente de Yango (bot) por cadena y un login para sus dueños.

Todos los locales de la cadena usan la misma cuenta, así que comparten una cola: el bot coloca un pedido a la vez
por cadena (carrito único). Las consultas (menú, cotización, geocodificación) sí van en paralelo.
"""
import re
from typing import Any, Dict, List, Optional

from app import db, partners

LOGIN_RE = re.compile(r"^[a-z0-9._@-]{3,60}$")
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _public(row) -> Optional[Dict[str, Any]]:
    d = db.row_dict(row)
    if d:
        d.pop("password_hash", None)
    return d


def get(chain_id: int) -> Optional[Dict[str, Any]]:
    return _public(db.one("SELECT * FROM chains WHERE id=?", (chain_id,)))


def get_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    return _public(db.one("SELECT * FROM chains WHERE slug=?", (slug,)))


def all_chains() -> List[Dict[str, Any]]:
    out = []
    for r in db.query("SELECT * FROM chains ORDER BY name"):
        c = _public(r)
        c["partners"] = [{"slug": p["slug"], "name": p["name"], "enabled": bool(p["enabled"])}
                         for p in locales(c["id"])]
        out.append(c)
    return out


def locales(chain_id: int) -> List[Dict[str, Any]]:
    return [db.row_dict(r, partners.JSON_FIELDS)
            for r in db.query("SELECT * FROM partners WHERE chain_id=? ORDER BY name", (chain_id,))]


def create(slug: str, name: str, account: str, login: str, password: str, contact: str = "") -> Dict[str, Any]:
    slug = (slug or "").strip().lower()
    login = (login or "").strip().lower()
    if not partners.SLUG_RE.match(slug):
        raise ValueError("Slug de cadena inválido: minúsculas, números y guiones")
    if not ACCOUNT_RE.match(account or ""):
        raise ValueError("Nombre de cuenta Yango inválido (letras, números, _ y -)")
    if not LOGIN_RE.match(login):
        raise ValueError("Usuario inválido (3–60: minúsculas, números, . _ @ -)")
    if len(password or "") < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    if len((name or "").strip()) < 2:
        raise ValueError("Falta el nombre de la cadena")
    if db.one("SELECT 1 FROM chains WHERE slug=? OR login=? OR account=?", (slug, login, account)):
        raise ValueError("Ya existe una cadena con ese slug, usuario o cuenta")
    now = db.now_iso()
    cid = db.execute(
        "INSERT INTO chains(slug, name, account, login, password_hash, contact, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (slug, name.strip(), account, login, partners.hash_password(password), (contact or "").strip()[:120],
         now, now))
    db.execute("INSERT OR IGNORE INTO accounts(name) VALUES (?)", (account,))
    return get(cid)


def update(chain_id: int, **fields: Any) -> Dict[str, Any]:
    c = get(chain_id)
    if not c:
        raise ValueError("Cadena no encontrada")
    sets, vals = [], []
    if "name" in fields and len(str(fields["name"]).strip()) >= 2:
        sets.append("name=?")
        vals.append(str(fields["name"]).strip())
    if "contact" in fields:
        sets.append("contact=?")
        vals.append(str(fields["contact"] or "").strip()[:120])
    if "account" in fields and fields["account"] != c["account"]:
        if not ACCOUNT_RE.match(fields["account"] or ""):
            raise ValueError("Nombre de cuenta Yango inválido")
        sets.append("account=?")
        vals.append(fields["account"])
    if "login" in fields and fields["login"] != c["login"]:
        login = str(fields["login"]).strip().lower()
        if not LOGIN_RE.match(login):
            raise ValueError("Usuario inválido")
        sets.append("login=?")
        vals.append(login)
    if fields.get("password"):
        if len(fields["password"]) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        sets.append("password_hash=?")
        vals.append(partners.hash_password(fields["password"]))
    if sets:
        sets.append("updated_at=?")
        vals.append(db.now_iso())
        try:
            db.execute(f"UPDATE chains SET {', '.join(sets)} WHERE id=?", (*vals, chain_id))
        except Exception as e:  # UNIQUE
            raise ValueError(f"No se pudo guardar: {e}")
    c = get(chain_id)
    if "account" in fields:
        # Los locales siguen la cuenta de la cadena (el bot es de la cadena, no del local).
        db.execute("INSERT OR IGNORE INTO accounts(name) VALUES (?)", (c["account"],))
        db.execute("UPDATE partners SET account=?, updated_at=? WHERE chain_id=?", (c["account"], db.now_iso(), chain_id))
    return c


def attach(chain_id: int, slug: str) -> Dict[str, Any]:
    """Pasa un local a la cadena: usa la cuenta (bot) de la cadena."""
    c = get(chain_id)
    if not c or not partners.get(slug):
        raise ValueError("Cadena o local no encontrado")
    db.execute("UPDATE partners SET chain_id=?, account=?, updated_at=? WHERE slug=?",
               (chain_id, c["account"], db.now_iso(), slug))
    return partners.get(slug)


def detach(slug: str) -> Dict[str, Any]:
    db.execute("UPDATE partners SET chain_id=NULL, updated_at=? WHERE slug=?", (db.now_iso(), slug))
    return partners.get(slug)


def authenticate(login: str, password: str) -> Optional[Dict[str, Any]]:
    r = db.one("SELECT * FROM chains WHERE login=?", ((login or "").strip().lower(),))
    if r and partners.check_password(password or "", r["password_hash"]):
        return _public(r)
    return None
