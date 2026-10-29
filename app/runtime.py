"""Lo que corre en segundo plano: fuente Yango, cotizaciones, cola de pedidos por cuenta, menú y sesiones."""
import asyncio
import logging
from typing import Any, Dict, Optional

from app import alerts, db, menu as menu_mod, orders, partners
from app.config import settings
from app.quotes import QuoteService
from bot import sessions
from bot.base import PlacementMismatch, SessionDown, YangoSource
from bot.login import LoginManager

log = logging.getLogger("runtime")


def make_source() -> YangoSource:
    if settings.yango_source == "yango_web":
        from bot.yango_web import YangoWebSource
        return YangoWebSource()
    from bot.mock import MockSource
    return MockSource()


class Dispatcher:
    """Una cola y un worker por cuenta: el carrito de cada cuenta admite un pedido a la vez."""

    def __init__(self, rt: "Runtime") -> None:
        self.rt = rt
        self.queues: Dict[str, asyncio.Queue] = {}
        self.workers: Dict[str, asyncio.Task] = {}
        self.retry_delays = [5, 20, 60]

    def enqueue(self, account: str, token: str) -> None:
        if account not in self.queues:
            self.queues[account] = asyncio.Queue()
            self.workers[account] = asyncio.create_task(self._worker(account), name=f"pedidos-{account}")
        self.queues[account].put_nowait(token)

    def recover(self) -> None:
        """Tras un reinicio: lo que quedó en cola o a medio colocar."""
        for o in db.query("SELECT token, account, status FROM orders WHERE status IN ('en_cola','colocando')"):
            if o["status"] == "colocando":
                # Pudo haberse creado en Yango antes de caer: no se reintenta solo.
                orders.set_status(o["token"], "revision", error="El servicio se reinició mientras se colocaba")
                continue
            self.enqueue(o["account"], o["token"])

    async def _worker(self, account: str) -> None:
        q = self.queues[account]
        while True:
            token = await q.get()
            try:
                await self.process(token)
            except Exception:  # pragma: no cover
                log.exception("worker %s", account)
            finally:
                q.task_done()

    async def process(self, token: str) -> None:
        o = orders.get(token)
        if not o or o["status"] != "en_cola":
            return
        partner = partners.get_by_id(o["partner_id"])
        label = f"#{o['number']} {partner['name']} {settings.currency}{o['total']:g}"
        if not settings.order_placement_enabled:
            orders.set_status(token, "manual", error="Colocación automática apagada")
            await alerts.alert("pedido_manual", f"[MANUAL] Pedido nuevo {label}: cargar a mano en Yango con la cuenta "
                                                f"{o['account']}.\nComentario: {o['yango_comment']}")
            return
        attempts = o["attempts"]
        while True:
            attempts += 1
            orders.set_status(token, "colocando", attempts=attempts)
            try:
                res = await self.rt.source.place_order(o["account"], partner, o)
                orders.set_status(token, "colocado", yango_order_nr=res.get("yango_order_nr"),
                                  yango_total=res.get("yango_total"), error=None)
                return
            except PlacementMismatch as e:
                orders.set_status(token, "revision", error=str(e))
                await alerts.alert("pedido_revision", f"[REVISIÓN] {label} en revisión: {e}")
                return
            except SessionDown as e:
                sessions.set_status(o["account"], "caida", str(e))
                orders.set_status(token, "manual", error=str(e))
                await alerts.alert("sesion_caida", f"[SESIÓN] Cuenta {o['account']} sin sesión ({e}). {label} pasa a manual. "
                                                   "Cargar cookies nuevas en Admin → Cuentas.")
                return
            except Exception as e:
                log.warning("colocar %s intento %s: %s", o["number"], attempts, e)
                if attempts >= settings.order_max_attempts:
                    orders.set_status(token, "manual", error=str(e)[:300])
                    await alerts.alert("pedido_fallido", f"[FALLÓ] {label}: el bot no pudo colocarlo ({e}). Cargar a mano.")
                    return
                orders.set_status(token, "en_cola", error=str(e)[:300], attempts=attempts)
                await asyncio.sleep(self.retry_delays[min(attempts - 1, len(self.retry_delays) - 1)])
                if (orders.get(token) or {}).get("status") != "en_cola":
                    return  # lo canceló o tomó un operador mientras esperaba

    def health(self) -> Dict[str, Any]:
        return {a: q.qsize() for a, q in self.queues.items()}


class Runtime:
    def __init__(self, source: Optional[YangoSource] = None) -> None:
        self.source = source or make_source()
        self.quotes = QuoteService(self.source.catalog)
        self.dispatcher = Dispatcher(self)
        self.login: Optional[LoginManager] = None
        self.tasks: list = []

    async def start(self, background: bool = True) -> None:
        await self.source.start()
        from bot.yango_web import load_config
        cfg = load_config()
        self.login = LoginManager({**cfg["login"], "logged_out_texts": cfg["session"]["logged_out_texts"]},
                                  browser=getattr(self.source, "browser", None))
        self.dispatcher.recover()
        if background:
            self.tasks.append(asyncio.create_task(self._menu_loop(), name="menu"))
            self.tasks.append(asyncio.create_task(self._session_loop(), name="sesiones"))

    async def stop(self) -> None:
        for t in self.tasks + list(self.dispatcher.workers.values()):
            t.cancel()
        if self.login:
            await self.login.stop()
        await self.source.stop()

    async def sync_partner(self, partner: Dict[str, Any]) -> Dict[str, Any]:
        """Resuelve el local (si falta) y actualiza el menú."""
        if not partner.get("place_slug") or partner.get("place_lat") is None:
            if not partner.get("yango_url"):
                raise ValueError("Falta la URL de Yango del restaurante")
            place = await self.source.resolve_place(partner["account"], partner["yango_url"])
            if not place.get("place_slug"):
                raise RuntimeError("Yango no devolvió el local")
            partner = partners.update(partner["slug"], place_slug=place["place_slug"], place_id=place["place_id"],
                                      place_lat=place["lat"], place_lon=place["lon"],
                                      place_address=place.get("address"))
            db.event("local_resuelto", place, partner_id=partner["id"])
        try:
            raw = await self.source.menu(partner["account"], partner["place_slug"],
                                         partner["place_lat"], partner["place_lon"])
            parsed = menu_mod.parse_menu_retrieve(raw)
            if not parsed["items"]:
                raise RuntimeError("Menú vacío")
            menu_mod.save(partner["id"], parsed)
            return {"items": len(parsed["items"]), "categories": len(parsed["categories"])}
        except Exception as e:
            menu_mod.save_error(partner["id"], str(e))
            raise

    async def _menu_loop(self) -> None:
        while True:
            for p in partners.all_partners():
                if p["enabled"] and (p.get("place_slug") or p.get("yango_url")):
                    try:
                        await self.sync_partner(p)
                    except Exception as e:
                        log.warning("menú %s: %s", p["slug"], e)
            await asyncio.sleep(settings.menu_sync_minutes * 60)

    async def _session_loop(self) -> None:
        notified: Dict[str, str] = {}
        while True:
            accounts = {p["account"] for p in partners.all_partners() if p["enabled"]}
            for a in sorted(accounts):
                try:
                    res = await self.source.check_session(a)
                except Exception as e:
                    res = {"ok": False, "detail": str(e)}
                sessions.set_status(a, "ok" if res["ok"] else "caida", res["detail"])
                if not res["ok"] and notified.get(a) != res["detail"]:
                    notified[a] = res["detail"]
                    await alerts.alert("sesion_caida", f"[SESIÓN] Cuenta {a}: {res['detail']}. Cargar cookies nuevas.")
                elif res["ok"]:
                    notified.pop(a, None)
            await asyncio.sleep(300)


runtime: Optional[Runtime] = None


def get_runtime() -> Runtime:
    assert runtime is not None, "runtime no iniciado"
    return runtime
