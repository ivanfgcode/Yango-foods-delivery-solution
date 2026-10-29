# Yango Foods · Pedidos propios

Web de pedidos propia para cada partner (`dominio.com/<restaurante>`) con el delivery hecho por Yango.
Cada partner tiene un **restaurante oculto** en Yango Foods, visible solo para una cuenta cliente nuestra
(una cuenta por restaurante), con **0 % de comisión**: Yango gana solo con el envío.

```
 Cliente ──► dominio.com/<slug>  ──►  API (FastAPI)  ──► cola por cuenta ──► BOT (Playwright) ──► food.yango.com
            menú · mapa · checkout     precios, validación,                  sesión de la cuenta     pedido en efectivo
                                       cotización en vivo  ◄──────────────── fetch() desde pestañas  con comentario
```

## Cómo funciona

1. **Catálogo**: el bot lee el menú del restaurante oculto de food.yango.com (`/api/v2/menu/retrieve/{local}`)
   cada `MENU_SYNC_MINUTES`. Platos, precios, promos, fotos y grupos de opciones son los de Yango, con los mismos
   ids que usa el carrito. El partner edita su menú en Yango, como siempre.
2. **Dirección**: el cliente mueve el mapa hasta su puerta (pin fijo al centro). El mapa es **Yandex Maps**
   (JS API 2.1): los mismos datos que usan Yango y Yango Maps, con calles y números de casa de Bolivia. Carga sin clave;
   `YANDEX_MAPS_API_KEY` (gratis en developer.tech.yandex.ru) es recomendable en producción. Leaflet/OSM solo si Yandex no carga. El punto se geocodifica con el geocodificador de Yango
   (`/eats/v1/persuggest/v1/finalsuggest`) y el cliente agrega número/referencia (obligatorio) y nota.
3. **Cotización**: `/api/v2/catalog/{local}?latitude&longitude&shippingType=delivery` da disponibilidad
   (zona y horario), tramos de envío por monto, pedido mínimo, tarifa de servicio, ETA y recargo. No toca el carrito,
   así que se hace en paralelo: 20 cotizaciones reales en 0,7 s. Caché por punto (~11 m) `QUOTE_CACHE_SECONDS`.
4. **Pedido**: el servidor recalcula todo (precios del menú, opciones obligatorias, envío) y rechaza si el total
   cambió respecto a lo que vio el cliente. Pago: **efectivo o QR al mensajero**.
5. **Colocación en Yango**: un worker por cuenta (el carrito de una cuenta es uno). Vacía el carrito, agrega los
   platos, compara subtotal y envío con lo cotizado (`FEE_TOLERANCE`) y hace el checkout con el comentario:
   `CLIENTE: Nombre Apellido | TEL: +591 7XXXXXXX | PAGO: QR al mensajero | REF: ... | NOTA: ... | WEB #ABC123`.
   Con `ORDER_PLACEMENT_ENABLED=false` (por defecto) el pedido queda **manual**: alerta por Telegram y un operador
   lo carga en Yango y lo marca en el panel.
6. La vendor app del restaurante y los mensajeros siguen el flujo normal de Yango: no se toca nada ahí.

| Estado | Qué significa |
|---|---|
| `en_cola` / `colocando` | El bot lo está creando en Yango |
| `colocado` | Creado en Yango (N° de pedido guardado) |
| `manual` | Colocación apagada, sesión caída o el bot falló 3 veces: cargarlo a mano |
| `revision` | Yango cobra distinto de lo cotizado o el servicio se reinició a mitad: revisar |
| `cancelado` | Cancelado por un operador |

## Pantallas

| Ruta | Para quién |
|---|---|
| `/<slug>` | Cliente: menú, buscador, carrito, checkout con mapa |
| `/<slug>/pedido/<token>` | Cliente: estado del pedido |
| `/<slug>/portal` | Partner: diseño (logo, portada, colores, tipografías, tamaños, cuadrícula/lista), textos, pausar pedidos, ver pedidos |
| `/admin` | Equipo: pedidos (marcar colocado, reintentar, copiar comentario), restaurantes, cuentas Yango (cookies), eventos |

## Sesiones de las cuentas

El login de Yango ID es por teléfono + SMS y a veces pide captcha. Dos formas de cargar la sesión de una cuenta:

* **Con teléfono (asistido)**: Admin → Cuentas Yango → *Iniciar sesión con teléfono*. El bot abre Yango ID, escribe
  el número (+591) y pide el SMS. El operador escribe el código; si aparece captcha u otra pantalla, la resuelve él
  tocando la captura y escribiendo (el bot no resuelve captchas). Al volver a food.yango.com con sesión, se guarda sola.
* **Cookies**: entrar con la cuenta en un navegador, exportar las cookies (Cookie-Editor → Export → JSON) y pegarlas
  en Admin → Cuentas Yango.

Cada 5 min se comprueba la sesión; si cae, alerta y los pedidos de esa cuenta pasan a manual.

## Local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
.venv/bin/python run.py --port 8000          # YANGO_SOURCE=mock: Yango simulado + restaurante demo
```
Abrir `http://127.0.0.1:8000/wrap-roll`, el portal `…/wrap-roll/portal` (contraseña `demo12345`) y
`http://127.0.0.1:8000/admin` (clave `dev-admin`).

Con Yango real sin cookies (solo restaurantes públicos): `YANGO_SOURCE=yango_web ALLOW_ANONYMOUS=true`.

## Railway

Servicio desde este repo (Dockerfile), **volumen en `/app/data`**, 1 réplica, variables de `.env.example`.

## Pendiente

- [ ] Grabar un pedido real con una cuenta (`python -m tools.record_traffic`) y ajustar `order` en
      `config/yango_web.yaml` (carrito, checkout, captura del N° de pedido). Hasta entonces: colocación manual.
- [ ] Cuentas cliente (una por restaurante) con acceso a los restaurantes ocultos y sus cookies.
- [ ] Primer login con teléfono de la cuenta de Wrap & Roll (confirmar `login.session_cookies` en `config/yango_web.yaml`).
- [ ] API key de Yandex Maps (opcional) y dominio propio.
- [ ] Seguimiento del estado del pedido en Yango para mostrarlo al cliente.
