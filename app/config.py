"""Configuración por variables de entorno (Railway). Nada de secretos en el repo."""
import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


ROOT = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
        self.db_path = Path(os.getenv("DB_PATH", str(self.data_dir / "pedidos.db")))
        self.sessions_dir = self.data_dir / "sessions"
        self.uploads_dir = self.data_dir / "uploads"
        self.debug_dir = self.data_dir / "debug"

        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.admin_key = os.getenv("ADMIN_KEY", "dev-admin")
        self.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

        # mock = Yango simulado (desarrollo y tests) | yango_web = food.yango.com real con Playwright
        self.yango_source = os.getenv("YANGO_SOURCE", "mock")
        self.yango_base_url = os.getenv("YANGO_BASE_URL", "https://food.yango.com").rstrip("/")
        self.yango_locale_path = os.getenv("YANGO_LOCALE_PATH", "es-bo")
        self.headless = _bool("HEADLESS", True)
        # Sin cookies cargadas, abrir food.yango.com sin sesión (solo ve restaurantes públicos). Para pruebas.
        self.allow_anonymous = _bool("ALLOW_ANONYMOUS", False)
        self.chromium_executable = os.getenv("CHROMIUM_EXECUTABLE", "")  # vacío = el de la imagen de Playwright
        self.pages_per_account = _int("PAGES_PER_ACCOUNT", 2)
        self.max_inflight_per_account = _int("MAX_INFLIGHT_PER_ACCOUNT", 12)
        self.bot_request_timeout = _float("BOT_REQUEST_TIMEOUT", 15.0)

        # Colocar pedidos reales en Yango. Apagado: el pedido queda en «manual» y se alerta para cargarlo a mano.
        self.order_placement_enabled = _bool("ORDER_PLACEMENT_ENABLED", False)
        self.order_max_attempts = _int("ORDER_MAX_ATTEMPTS", 3)
        # Diferencia máxima (Bs) entre el envío cotizado al cliente y el que muestra Yango al pagar.
        self.fee_tolerance = _float("FEE_TOLERANCE", 2.0)

        self.quote_cache_seconds = _int("QUOTE_CACHE_SECONDS", 90)
        self.quote_precision = _int("QUOTE_PRECISION", 4)  # decimales de lat/lon para la caché (~11 m)
        self.menu_sync_minutes = _int("MENU_SYNC_MINUTES", 15)

        # Zona de servicio (Bolivia). Fuera de esto ni se consulta a Yango.
        self.bounds = (
            _float("BOUNDS_LAT_MIN", -23.0), _float("BOUNDS_LAT_MAX", -9.6),
            _float("BOUNDS_LON_MIN", -69.7), _float("BOUNDS_LON_MAX", -57.4),
        )
        self.phone_prefix = os.getenv("PHONE_PREFIX", "+591")
        self.currency = os.getenv("CURRENCY_SYMBOL", "Bs")
        self.timezone = os.getenv("TZ_NAME", "America/La_Paz")

        # Mapa del checkout: Yandex Maps (los datos de Yango Maps). La clave es opcional pero recomendada en producción.
        self.yandex_maps_api_key = os.getenv("YANDEX_MAPS_API_KEY", "")
        self.yandex_maps_lang = os.getenv("YANDEX_MAPS_LANG", "es_ES")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        # Anti abuso: pedidos por IP en la ventana.
        self.orders_per_ip = _int("ORDERS_PER_IP", 8)
        self.orders_per_ip_window = _int("ORDERS_PER_IP_WINDOW", 600)

        self.demo_seed = _bool("DEMO_SEED", self.yango_source == "mock")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.sessions_dir, self.uploads_dir, self.debug_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
