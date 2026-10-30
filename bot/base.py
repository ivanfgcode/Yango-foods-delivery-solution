"""Interfaz de la fuente Yango (real con Playwright o simulada)."""
from typing import Any, Dict, Optional


class SessionDown(RuntimeError):
    """La cuenta no tiene sesión válida: hay que volver a cargar las cookies."""


class PlacementMismatch(RuntimeError):
    """Yango muestra otro precio/envío que el cotizado o falta un plato: el pedido va a revisión."""


class YangoSource:
    name = "base"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def resolve_place(self, account: str, yango_url: str) -> Dict[str, Any]:
        """URL de marca → {place_slug, place_id, name, lat, lon, address, logo_url, accent_color}."""
        raise NotImplementedError

    async def catalog(self, account: str, place_slug: str, lat: float, lon: float) -> Dict[str, Any]:
        """JSON crudo de /api/v2/catalog/{place_slug} para ese punto (cotización)."""
        raise NotImplementedError

    async def menu(self, account: str, place_slug: str, lat: float, lon: float) -> Dict[str, Any]:
        """JSON crudo de /api/v2/menu/retrieve/{place_slug}."""
        raise NotImplementedError

    async def reverse_geocode(self, account: str, lat: float, lon: float) -> Dict[str, Any]:
        """JSON crudo de /eats/v1/persuggest/v1/finalsuggest (pin_drop)."""
        raise NotImplementedError

    async def brand_place(self, account: str, brand_slug: str, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """JSON crudo de eats-catalog/v2/brand/place para ese punto, o None si ninguna sucursal llega."""
        raise NotImplementedError

    async def search_address(self, account: str, text: str, lat: float, lon: float) -> Dict[str, Any]:
        """JSON crudo de /eats/v1/persuggest/v1/suggest (lo que el cliente escribe)."""
        raise NotImplementedError

    async def check_session(self, account: str) -> Dict[str, Any]:
        """{'ok': bool, 'detail': str}"""
        raise NotImplementedError

    async def place_order(self, account: str, partner: Dict[str, Any], order: Dict[str, Any],
                          dry_run: bool = False) -> Dict[str, Any]:
        """Crea el pedido en Yango con la cuenta. Devuelve {'yango_order_nr', 'yango_total', 'delivery_fee'}.
        dry_run: todo menos el botón final; deja el carrito y el NIT de la cuenta vacíos."""
        raise NotImplementedError

    async def vendor_active(self, account: str) -> Dict[str, Any]:
        """Vendor app del local (vendor.yango.com, sesión del restaurante): JSON crudo de los pedidos activos."""
        raise NotImplementedError

    async def vendor_order(self, account: str, order_nr: str) -> Optional[Dict[str, Any]]:
        """Vendor app: detalle de un pedido (cómo terminó uno que ya no está activo)."""
        raise NotImplementedError

    async def order_tracking(self, account: str) -> Dict[str, Any]:
        """Cuenta cliente que colocó el pedido (food.yango.com): JSON crudo del seguimiento de sus pedidos."""
        raise NotImplementedError

    def health(self) -> Dict[str, Any]:
        return {"source": self.name}
