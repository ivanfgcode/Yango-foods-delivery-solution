# Menu Deli Bo (pedidos propios con Yango): guía para agentes

Lee [README.md](README.md) primero. Proyecto hermano: `ivanfgcode/restotec-yango` (mismo estilo de bot y operación).

## Reglas
* Los precios y el envío se calculan siempre en el servidor (menú sincronizado + cotización de Yango). Nunca confiar en el navegador.
* El bot no resuelve captchas. Sesiones: login asistido por teléfono (el operador escribe el SMS y resuelve el captcha desde Admin → Cuentas) o cookies JSON.
* Mapa: Yandex Maps (los datos de Yango Maps). Nunca reutilizar la clave de mapas de food.yango.com.
* Panel del partner (Menu Deli Bo): `app/templates/portal.html` + `app/static/portal.css`; plan y acuerdo en `app/billing.py` (si cambia el texto del acuerdo, subir `TERMS_VERSION`).
* Admin: lenguaje «Instrumental» del picker de restotec-yango (`app/static/admin.css` + lienzos en `app/static/charts.js`). Los números del Resumen salen de `app/dashboard.py`.
* Íconos: Lucide en `app/static/icons.svg` (`<use href="/static/icons.svg#nombre">`). Sin emojis en la interfaz ni en las alertas.
* El bot nunca pulsa nada de `order.never_click` ni paga con tarjeta. Pago siempre efectivo/QR al mensajero.
* La sucursal la decide Yango con la dirección (`app/branches.py`); el servidor la recalcula al crear el pedido. Lo configurado (más vendidos, TikTok) usa ids de la sucursal principal y se traduce por nombre.
* Un pedido a la vez por cuenta (carrito único). Las consultas (catálogo, menú, geocodificación) sí en paralelo.
* Lo que viene de food.yango.com se valida con fixtures reales en `tests/fixtures/`. Si cambia el formato, actualizar fixture y parser juntos.
* Nunca commitear `.env`, `data/` (base, sesiones, subidas) ni credenciales. Secretos en variables de Railway.
* Valores del tema que se inyectan en CSS pasan por `themes.validate` (hex, listas cerradas, https).

## Comandos
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
.venv/bin/python run.py --port 8000
```
