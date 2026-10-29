# Pedidos propios (Yango Foods): guía para agentes

Lee [README.md](README.md) primero. Proyecto hermano: `ivanfgcode/restotec-yango` (mismo estilo de bot y operación).

## Reglas
* Los precios y el envío se calculan siempre en el servidor (menú sincronizado + cotización de Yango). Nunca confiar en el navegador.
* El bot no resuelve captchas ni inicia sesión por SMS: las sesiones entran como cookies JSON (Admin → Cuentas).
* El bot nunca pulsa nada de `order.never_click` ni paga con tarjeta. Pago siempre efectivo/QR al mensajero.
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
