/* Service worker de la web del cliente: muestra los avisos del pedido (Web Push) y abre su seguimiento al tocarlos. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {title: "Tu pedido", body: e.data && e.data.text()}; }
  e.waitUntil((async () => {
    await self.registration.showNotification(d.title || "Tu pedido", {
      body: d.body || "", icon: d.icon || undefined, badge: d.icon || undefined,
      tag: d.tag || "pedido", renotify: true, data: {url: d.url || "/"},
    });
    // Si la página del pedido está abierta, que se actualice sola.
    for (const c of await self.clients.matchAll({type: "window", includeUncontrolled: true})) c.postMessage({type: "order-update"});
  })());
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    for (const c of await self.clients.matchAll({type: "window", includeUncontrolled: true})) {
      if (new URL(c.url).pathname === url && "focus" in c) return c.focus();
    }
    return self.clients.openWindow(url);
  })());
});
