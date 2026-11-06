"""SQLite (WAL) en el volumen de Railway. Un solo proceso: el bot y la API comparten la conexión."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,            -- ruta pública: dominio.com/<slug>
    name TEXT NOT NULL,
    yango_url TEXT,                       -- URL del restaurante oculto en food.yango.com
    brand_slug TEXT,
    place_slug TEXT,                      -- slug del local (lo resuelve el bot)
    place_id INTEGER,
    place_lat REAL,
    place_lon REAL,
    place_address TEXT,
    account TEXT NOT NULL,                -- cuenta cliente de Yango que ve este restaurante
    enabled INTEGER NOT NULL DEFAULT 1,
    accepting_orders INTEGER NOT NULL DEFAULT 1,
    theme TEXT NOT NULL DEFAULT '{}',
    profile TEXT NOT NULL DEFAULT '{}',   -- textos, redes, WhatsApp
    portal_password_hash TEXT,
    min_order REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS menus (
    partner_id INTEGER PRIMARY KEY REFERENCES partners(id),
    data TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    token TEXT PRIMARY KEY,
    number TEXT UNIQUE NOT NULL,          -- número corto que ve el cliente
    partner_id INTEGER NOT NULL REFERENCES partners(id),
    account TEXT NOT NULL,
    status TEXT NOT NULL,
    customer TEXT NOT NULL,
    address TEXT NOT NULL,
    items TEXT NOT NULL,
    payment TEXT NOT NULL,
    subtotal REAL NOT NULL,
    delivery_fee REAL NOT NULL,
    service_fee REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL,
    quote TEXT,
    yango_comment TEXT NOT NULL,
    yango_order_nr TEXT,
    yango_total REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    client_ip TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_partner ON orders(partner_id, created_at);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    order_token TEXT,
    partner_id INTEGER,
    account TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_order ON events(order_token);
CREATE INDEX IF NOT EXISTS events_at ON events(at);

CREATE TABLE IF NOT EXISTS accounts (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'sin_sesion',   -- sin_sesion | ok | caida | error
    detail TEXT,
    session_imported_at TEXT,
    checked_at TEXT
);

-- Cadena de restaurantes: un bot (cuenta cliente de Yango) por cadena y un login para sus dueños.
CREATE TABLE IF NOT EXISTS chains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    account TEXT UNIQUE NOT NULL,         -- cuenta Yango (bot) de toda la cadena
    login TEXT UNIQUE NOT NULL,           -- usuario del portal de la cadena
    password_hash TEXT NOT NULL,
    contact TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Operadores del call center (cabina de llamadas).
CREATE TABLE IF NOT EXISTS operators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator TEXT NOT NULL,
    phone TEXT,
    partner_id INTEGER,
    order_token TEXT,
    outcome TEXT,                         -- pedido | consulta | reclamo | sin_cobertura | colgo | otro
    notes TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS calls_at ON calls(started_at);

-- Ajustes editables desde el admin (guion de llamadas, etc.).
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Columnas agregadas después de la primera versión: se añaden a bases existentes al arrancar.
MIGRATIONS = (
    ("partners", "chain_id", "INTEGER REFERENCES chains(id)"),
    ("partners", "commission_rate", "REAL"),
    ("partners", "pdf_style", "TEXT NOT NULL DEFAULT '{}'"),
    ("orders", "commission", "REAL NOT NULL DEFAULT 0"),
    ("orders", "yango_fee", "REAL"),
    ("orders", "channel", "TEXT NOT NULL DEFAULT 'web'"),
    ("orders", "operator", "TEXT"),
    ("accounts", "yango_phone", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, ddl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS partners_chain ON partners(chain_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS orders_phone ON orders(json_extract(customer, '$.phone'))")

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            settings.ensure_dirs()
            _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("PRAGMA busy_timeout=5000")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


def reset_for_tests() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connect()
    with _lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def query(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    with _lock:
        return connect().execute(sql, params).fetchall()


def one(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    with _lock:
        return connect().execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = connect().execute(sql, params)
        return cur.lastrowid or cur.rowcount


def event(kind: str, detail: Any = None, order_token: Optional[str] = None,
          partner_id: Optional[int] = None, account: Optional[str] = None) -> None:
    execute(
        "INSERT INTO events(at, kind, order_token, partner_id, account, detail) VALUES (?,?,?,?,?,?)",
        (now_iso(), kind, order_token, partner_id, account,
         json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None),
    )


def kv_get(key: str, default: Any = None) -> Any:
    r = one("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value: Any) -> None:
    execute("INSERT INTO kv(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), now_iso()))


def row_dict(row: Optional[sqlite3.Row], json_fields: tuple = ()) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        if d.get(f):
            d[f] = json.loads(d[f])
    return d
