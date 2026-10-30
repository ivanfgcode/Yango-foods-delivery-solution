# Menu Deli Bo · Pedidos propios con delivery de Yango

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
2. **Ubicación primero y sucursal**: al entrar, la tienda pregunta «¿Dónde te lo llevamos?». Con el punto,
   `eats-catalog/v2/brand/place?brand_slug&latitude&longitude` devuelve la sucursal de la marca que atiende ahí
   (centro de Santa Cruz → Equipetrol, sur → Palmas…; 404 = ninguna llega) y la tienda muestra el menú, los
   precios y el envío de esa sucursal. El pedido se cotiza y se coloca en esa sucursal (el servidor la vuelve a
   calcular con la dirección). Cada sucursal tiene sus propios ids en Yango: más vendidos, TikTok y upselling se
   configuran una vez y se traducen por nombre de producto; el carrito también pasa de sucursal por nombre.
3. **Dirección**: el cliente escribe su calle (buscador de Yango, `/eats/v1/persuggest/v1/suggest`, solo resultados cerca
   de la sucursal) o mueve el mapa hasta su puerta (pin fijo al centro). El mapa muestra la sucursal a la que pide. El mapa es **Yandex Maps**
   (JS API 2.1): los mismos datos que usan Yango y Yango Maps, con calles y números de casa de Bolivia. Carga sin clave;
   `YANDEX_MAPS_API_KEY` (gratis en developer.tech.yandex.ru) es recomendable en producción. Leaflet/OSM solo si Yandex no carga. El punto se geocodifica con el geocodificador de Yango
   (`/eats/v1/persuggest/v1/finalsuggest`) y el cliente agrega número/referencia (obligatorio) y nota.
4. **Disponibilidad**: Yango marca igual («no disponible por horario») el local cerrado y la zona cerrada por demanda.
   Se distingue cotizando en la ubicación de la propia sucursal: si ahí atiende, el cliente ve «Por alta demanda no
   llegamos a esta zona ahora»; si no, «El restaurante está cerrado ahora» (estado real del local en Yango).
   Fuera del área de reparto: «No llegamos a esta dirección». Yango también avisa si otra sucursal de la marca atiende
   ese punto (`other_branch`); por ahora cada partner es una sola sucursal.
5. **Cotización**: `/api/v2/catalog/{local}?latitude&longitude&shippingType=delivery` da disponibilidad
   (zona y horario), tramos de envío por monto, pedido mínimo, tarifa de servicio, ETA y recargo. No toca el carrito,
   así que se hace en paralelo: 20 cotizaciones reales en 0,7 s. Caché por punto (~11 m) `QUOTE_CACHE_SECONDS`.
6. **Pedido**: el servidor recalcula todo (precios del menú, opciones obligatorias, envío) y rechaza si el total
   cambió respecto a lo que vio el cliente. Pago: **efectivo o QR al mensajero**.
7. **Colocación en Yango**: un worker por cuenta (el carrito de una cuenta es uno). Vacía el carrito, agrega los
   platos, compara subtotal y envío con lo cotizado (`FEE_TOLERANCE`) y hace el checkout con el comentario:
   `CLIENTE: Nombre Apellido | TEL: +591 7XXXXXXX | PAGO: QR al mensajero | REF: ... | NOTA: ... | WEB #ABC123`.
   Con `ORDER_PLACEMENT_ENABLED=false` (por defecto) el pedido queda **manual**: alerta por Telegram y un operador
   lo carga en Yango y lo marca en el panel.
8. **Upselling**: en la ficha de cada producto, «Otras personas lo combinaron con», calculado con los pedidos de la
   web de ese restaurante (veces juntos / veces pedido, mínimo `UPSELL_MIN_PAIRS`, últimos `UPSELL_DAYS` días).
   Sin historial, y al cerrar el carrito si no lleva bebida, sugiere bebidas del menú (las más vendidas primero).
   Las combinaciones más frecuentes se ven en Admin → Resumen → Más vendido.
9. **Más vendidos y TikTok** (Admin → Tienda, o el portal del partner): arriba del menú, un carrusel «Más vendidos»
   automático (productos en más pedidos de la web, sin bebidas; sin datos, la categoría «Populares» de Yango) o
   elegido a mano, y debajo los videos de TikTok: se pega el enlace (vt./vm. sirven) y se etiquetan uno o varios
   productos con el selector (foto, nombre, precio). El video se reproduce con el reproductor de TikTok y trae «Pedir».
   No hace falta app de desarrollador (oEmbed público); listar los videos de la cuenta solos sí la necesitaría.
8. La vendor app del restaurante y los mensajeros siguen el flujo normal de Yango: no se toca nada ahí.

| Estado | Qué significa |
|---|---|
| `en_cola` / `colocando` | El bot lo está creando en Yango |
| `colocado` | Creado en Yango (N° de pedido guardado) |
| `manual` | Colocación apagada, sesión caída o el bot falló 3 veces: cargarlo a mano |
| `revision` | Yango cobra distinto de lo cotizado o el servicio se reinició a mitad: revisar |
| `cancelado` | Cancelado por un operador |

## Landing (`/`)

Página de venta de Menu Deli Bo para restaurantes: qué resuelve (atender chats, tiempo de respuesta, menú viejo, buscar
mensajero, errores, comisiones), antes/después de un pedido, funciones con capturas reales (`app/static/landing/`),
calculadora de ahorro, precio con los meses pagados por Yango, preguntas y formulario. Cada interesado queda en
Admin → Eventos (`lead`) y llega por Telegram. `SALES_WHATSAPP` (591…) agrega el botón de WhatsApp.

## Pantallas

| Ruta | Para quién |
|---|---|
| `/<slug>` | Cliente: menú, buscador, carrito, checkout con mapa |
| `/<slug>/pedido/<token>` | Cliente: estado del pedido |
| `/<slug>/portal` | Partner: diseño (logo, portada, colores, tipografías, tamaños, cuadrícula/lista), textos, videos de TikTok con su producto, pausar pedidos, ver pedidos |
| `/admin` | Equipo: **Resumen** (ventas, pedidos, por atender, ticket y envío medio, lo más vendido, restaurantes, hora pico, mejor día; por periodo y restaurante), pedidos (marcar colocado, reintentar, copiar comentario), restaurantes, cuentas Yango (SMS o cookie), eventos |

## Plan Menu Deli Bo (monetización)

* **Precio**: `PLAN_PRICE_BS` (Bs150) por sucursal y por mes; 0 % de comisión por pedido.
* **Meses pagados por Yango**: `PLAN_SPONSORED_MONTHS` (3) al empezar. **Permanencia**: `PLAN_MIN_PAID_MONTHS` (3) meses
  pagados después del patrocinio; luego mes a mes.
* El partner ve en su panel (`/<slug>/portal`) el plan, el calendario de cobros, sus sucursales y el **acuerdo**, que acepta
  con su nombre (se guarda fecha, IP y versión). Cobro manual por QR o transferencia (`PLAN_PAYMENT_INFO`).
* El equipo ajusta cada plan en Admin → Restaurantes → **Plan** (precio, sucursales, meses de Yango, permanencia, inicio y
  «pagado hasta»). Estados: pagado por Yango, activo, pago pendiente, sin aceptar.
* El texto del acuerdo está en `app/templates/portal.html` (versión `billing.TERMS_VERSION`): revisarlo con un abogado
  antes de usarlo con clientes; si cambia, subir la versión para que se vuelva a aceptar.

## Sesiones de las cuentas

El login de Yango ID es por teléfono + SMS y a veces pide captcha. Admin → Cuentas Yango → *Entrar a una cuenta*
tiene dos modos (también desde cada fila de la tabla de cuentas):

* **Con SMS (asistido)**: El bot abre Yango ID, escribe
  el número (+591) y pide el SMS. El operador escribe el código; si aparece captcha u otra pantalla, la resuelve él
  tocando la captura y escribiendo (el bot no resuelve captchas). Al volver a food.yango.com con sesión, se guarda sola.
* **Con cookie**: entrar con la cuenta en un navegador y pegar (o subir) las cookies: JSON de Cookie-Editor
  (Export → JSON), `storage_state` de Playwright o la línea `Cookie:` de una petición a food.yango.com. Después se
  prueba la sesión sola.

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
