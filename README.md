# Yango Foods · Pedidos propios

Web de pedidos propia para cada partner (`dominio.com/<restaurante>`) con el delivery hecho por Yango.
Cada partner tiene un **restaurante oculto** en Yango Foods, visible solo para una cuenta cliente nuestra
(**un bot = una cuenta por cadena de restaurantes**, compartida por todos sus locales).

## Modelo comercial

- **Comisión del 3 %** sobre el subtotal de cada pedido, que paga el restaurante a Yango (`COMMISSION_RATE`,
  editable por restaurante en Admin → Restaurantes). **Sin cargos fijos**: el cliente no paga tarifa de servicio
  (`CHARGE_SERVICE_FEE=false`). Admin → Comisiones y el portal de la cadena muestran lo que se debe por mes.
- **Envío por distancia**, con mínimo y máximo por tramo (`DELIVERY_TIERS`):

  | Distancia | Envío |
  |---|---|
  | Hasta 1 km | Bs5 – 10 |
  | 1 a 3 km | Bs10 – 15 |
  | Más de 3 km | Bs15 – 25 |

  Dentro del tramo el precio sube con la distancia (la que da Yango en la cotización) y con la demanda (recargo de
  Yango, `DELIVERY_DEMAND_WEIGHT`): solo llega al máximo lejos y con alta demanda. La tienda muestra el rango
  siempre y el precio exacto al marcar la dirección. Con `DELIVERY_FEE_MODE=yango` se cobra el envío de Yango
  y los tramos quedan solo como referencia.

```
 Cliente ──► dominio.com/<slug>  ──►  API (FastAPI)  ──► cola por cuenta ──► BOT (Playwright) ──► food.yango.com
            menú · mapa · checkout     precios, validación,                  sesión de la cuenta     pedido en efectivo
                                       cotización en vivo  ◄──────────────── fetch() desde pestañas  con comentario
```

## Cómo funciona

1. **Catálogo**: el bot lee el menú del restaurante oculto de food.yango.com (`/api/v2/menu/retrieve/{local}`)
   cada `MENU_SYNC_MINUTES`. Platos, precios, promos, fotos y grupos de opciones son los de Yango, con los mismos
   ids que usa el carrito. El partner edita su menú en Yango, como siempre.
2. **Dirección**: el cliente mueve el mapa hasta su puerta (pin fijo al centro; Yandex Maps con
   `YANDEX_MAPS_API_KEY`, si no Leaflet/OSM). El punto se geocodifica con el geocodificador de Yango
   (`/eats/v1/persuggest/v1/finalsuggest`) y el cliente agrega número/referencia (obligatorio) y nota.
3. **Cotización**: `/api/v2/catalog/{local}?latitude&longitude&shippingType=delivery` da disponibilidad
   (zona y horario), tramos de envío por monto, pedido mínimo, tarifa de servicio, ETA y recargo. No toca el carrito,
   así que se hace en paralelo: 20 cotizaciones reales en 0,7 s. Caché por punto (~11 m) `QUOTE_CACHE_SECONDS`.
4. **Pedido** (web o por teléfono desde la cabina): el servidor recalcula todo (precios del menú, opciones
   obligatorias, envío del modelo) y rechaza si el total cambió respecto a lo que vio el cliente.
   Pago: **efectivo o QR al mensajero**.
5. **Colocación en Yango**: un worker por cuenta (el carrito de una cuenta es uno). Vacía el carrito, agrega los
   platos, compara subtotal y envío con lo cotizado (`FEE_TOLERANCE`) y hace el checkout con el comentario:
   `CLIENTE: Nombre Apellido | TEL: +591 7XXXXXXX | PAGO: QR al mensajero | COBRAR: Bs58 | REF: ... | NOTA: ... | WEB #ABC123`
   (`TEL #…` si entró por la cabina). El control de envío del bot compara con lo que cotizó Yango (`yango_fee`),
   no con el envío del modelo que paga el cliente.
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
| `/cadena` | Dueños de la cadena (usuario + contraseña): sus locales, estado del bot, pedidos y comisión del mes; entra al portal de cada local |
| `/operador` | Call center: cabina para atender llamadas con guion paso a paso y cargar pedidos rápido con teclado |
| `/admin` | Equipo: pedidos, restaurantes, cadenas, cuentas Yango (cookies y celular de login), comisiones, **Funciones** (menú PDF, operadores, guion de llamadas), eventos |

### Cadenas

Admin → Cadenas: nombre, **cuenta Yango (bot)**, usuario y contraseña. Los locales de la cadena usan su bot (al
cambiar el bot de la cadena cambian todos). Como el carrito de una cuenta es uno, el bot coloca los pedidos de la
cadena de a uno; las cotizaciones y el menú siguen en paralelo. Una cuenta no se comparte entre cadenas.

### Cabina de operadores

Admin → Funciones → Operadores crea los usuarios. En `/operador`: **F2** nueva llamada, el celular trae al cliente
conocido (nombre, últimas direcciones), el mapa cotiza el envío en vivo, **/** busca platos (↑↓ y Enter, opciones
con Enter), **Ctrl+Enter** confirma. A la derecha el guion de la llamada con los datos rellenados (restaurante,
cliente, envío, rango, total, ETA, número de pedido); **Alt+→** pasa de paso. Al final se registra el resultado
(pedido, consulta, reclamo, sin cobertura…). El guion se edita en Admin → Funciones → Guion de llamadas.

### Menú PDF

Admin → Funciones → Menú PDF: plantilla (clásico, moderno, minimal, pizarra), colores, letras, columnas, hoja
(A4/Carta/A5, vertical/horizontal), fotos, descripciones, promos, logo, QR a la web y categorías. Vista previa en
vivo; «Descargar PDF» lo genera en el servidor con Chromium (Playwright de la imagen) y «Imprimir» sirve como
respaldo desde el navegador. Usa los precios del menú sincronizado.

## Sesiones de las cuentas

El login de Yango ID es por teléfono + SMS y pide captcha: **el bot no inicia sesión solo**. Se entra con la cuenta
en un navegador, se exportan las cookies (extensión Cookie-Editor → Export → JSON) y se pegan en
Admin → Cuentas Yango (ahí también se anota el celular con el que se entra a cada cuenta). Cada 5 min se comprueba la sesión; si cae, alerta y los pedidos de esa cuenta pasan a manual.

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
- [ ] Cuentas cliente (una por cadena) con acceso a los restaurantes ocultos y sus cookies.
- [ ] Confirmar con Yango cómo cobra el mensajero cuando el envío del modelo difiere del de Yango
      (hoy va `COBRAR: Bs…` en el comentario) y cómo se factura la comisión del 3 %.
- [ ] API key de Yandex Maps y dominio propio.
- [ ] Seguimiento del estado del pedido en Yango para mostrarlo al cliente.
