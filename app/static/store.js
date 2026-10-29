/* Tienda: menú, carrito, checkout con mapa (pin fijo al centro) y cotización en vivo con Yango. */
(() => {
  const S = window.STORE;
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const money = (n) => `${S.currency}${Number(n).toLocaleString("es-BO", {maximumFractionDigits: 2})}`;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  const CART_KEY = `cart:${S.slug}`, PROFILE_KEY = "customer";

  let menu = null;
  let cart = store.get(CART_KEY, []);
  let view = "cart";
  let point = null, quote = null, quoteSeq = 0, addrDirty = false, map = null;

  // ------------------------------------------------------------------ util
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 1800); }
  async function api(path, body) {
    const r = await fetch(`/api/${S.slug}${path}`, body === undefined ? {} : {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(body)});
    let data = {}; try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error(data.detail || "Algo salió mal, intenta de nuevo");
    return data;
  }
  function debounce(fn, ms) { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; }
  function open(el) { $("#scrim").classList.add("on"); el.classList.add("on"); document.body.style.overflow = "hidden"; }
  function closeAll() { document.querySelectorAll(".sheet.on").forEach((e) => e.classList.remove("on")); $("#scrim").classList.remove("on"); document.body.style.overflow = ""; }
  $("#scrim").onclick = closeAll;
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeAll));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAll(); });

  // ------------------------------------------------------------------ menú
  function itemPrice(it) { return it.promo_price != null ? it.promo_price : it.price; }
  function lineAmount(l) {
    const it = menu.items[l.item_id]; if (!it) return 0;
    let unit = itemPrice(it);
    for (const g of it.groups) for (const oid of l.options[g.id] || []) { const o = g.options.find((x) => x.id === oid); if (o) unit += o.price; }
    return unit * l.quantity;
  }
  const subtotal = () => cart.reduce((s, l) => s + lineAmount(l), 0);

  function renderMenu() {
    const cats = menu.categories.filter((c) => c.items.some((id) => menu.items[id]));
    $("#cats").innerHTML = cats.map((c, i) => `<a href="#c${i}" data-i="${i}">${esc(c.name)}</a>`).join("");
    $("#menu").innerHTML = cats.map((c, i) => `
      <section class="section" id="c${i}"><h2>${esc(c.name)}</h2><div class="grid">
        ${c.items.map((id) => menu.items[id]).filter(Boolean).map(card).join("")}
      </div></section>`).join("") || `<p class="note" style="padding:40px 0">El menú está vacío por ahora.</p>`;
    document.querySelectorAll(".card[data-id]").forEach((el) => (el.onclick = () => openItem(el.dataset.id)));
    spyCategories();
  }
  const norm = (t) => String(t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  $("#search").addEventListener("input", debounce(() => {
    const q = norm($("#search").value.trim());
    document.querySelectorAll(".section").forEach((sec) => {
      let any = false;
      sec.querySelectorAll(".card[data-id]").forEach((c) => {
        const it = menu.items[c.dataset.id]; const hit = !q || norm(it.name + " " + it.description).includes(q);
        c.classList.toggle("hidden-by-search", !hit); any = any || hit;
      });
      sec.classList.toggle("hidden-by-search", !any);
    });
  }, 120));
  function card(it) {
    const promo = it.promo_price != null && it.promo_price < it.price;
    return `<button type="button" class="card${it.available ? "" : " off"}" data-id="${esc(it.id)}" ${it.available ? "" : "disabled"}>
      <div class="ph${it.image ? "" : " empty"}">${it.image ? `<img loading="lazy" src="${esc(it.image)}" alt="">` : esc(it.name[0] || "")}</div>
      <div class="bd"><h3>${esc(it.name)}</h3>${it.description ? `<div class="ds">${esc(it.description)}</div>` : ""}
        <div class="ft"><span class="price${promo ? " promo" : ""}">${money(itemPrice(it))}${promo ? `<s>${money(it.price)}</s>` : ""}</span>
        ${it.available ? `<span class="add" aria-hidden="true">+</span>` : `<small class="note">Agotado</small>`}</div></div></button>`;
  }
  function spyCategories() {
    const links = [...document.querySelectorAll("#cats a")];
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === `#${e.target.id}`)); const on = links.find((a) => a.classList.contains("on")); on && on.scrollIntoView({block: "nearest", inline: "center"}); } });
    }, {rootMargin: "-130px 0px -65% 0px"});
    document.querySelectorAll(".section").forEach((s) => obs.observe(s));
  }

  // ------------------------------------------------------------------ producto
  let cur = null;
  function openItem(id) {
    const it = menu.items[id]; if (!it || !it.available) return;
    cur = {it, qty: 1, sel: {}};
    $("#itemName").textContent = "";
    $("#itemBody").innerHTML = `${it.image ? `<div class="item-img"><img src="${esc(it.image)}" alt=""></div>` : ""}
      <div class="item-top"><h2>${esc(it.name)}</h2><span class="price">${money(itemPrice(it))}</span></div>
      ${it.description ? `<p class="item-ds">${esc(it.description)}</p>` : ""}
      ${it.groups.map((g) => {
        const need = Math.max(g.min, g.required ? 1 : 0), single = g.max === 1;
        const rule = single ? (need ? "Elige 1" : "Opcional") : g.max ? `${need ? `Mín. ${need} · ` : ""}Máx. ${g.max}` : need ? `Mín. ${need}` : "Opcional";
        return `<div class="grp" data-g="${esc(g.id)}"><div class="grp-h"><b>${esc(g.name)}</b><span class="${need ? "req" : ""}">${rule}</span></div>
          <div class="chips-opts">${g.options.map((o) => `<label class="optc"><input type="${single && need ? "radio" : "checkbox"}" name="g${esc(g.id)}" value="${esc(o.id)}">
            <span>${esc(o.name)}</span>${o.price ? `<span class="pr">+${money(o.price)}</span>` : ""}</label>`).join("")}</div></div>`;
      }).join("")}`;
    $("#itemBody").querySelectorAll(".grp").forEach((gEl) => gEl.addEventListener("change", (e) => {
      const g = it.groups.find((x) => x.id === gEl.dataset.g);
      let checked = [...gEl.querySelectorAll("input:checked")];
      if (g.max === 1 && e.target.type === "checkbox" && e.target.checked) { checked.forEach((c) => c !== e.target && (c.checked = false)); checked = [e.target]; }
      if (g.max > 1 && checked.length > g.max) { e.target.checked = false; toast(`Máximo ${g.max}`); checked = checked.filter((c) => c !== e.target); }
      cur.sel[g.id] = checked.map((c) => c.value); refreshItem();
    }));
    $("#itemErr").textContent = "";
    refreshItem(); open($("#itemSheet")); $("#itemBody").scrollTop = 0;
  }
  function missingGroup() {
    for (const g of cur.it.groups) { const need = Math.max(g.min, g.required ? 1 : 0); if ((cur.sel[g.id] || []).length < need) return g; }
    return null;
  }
  function refreshItem() {
    $("#qVal").textContent = cur.qty;
    const amt = lineAmount({item_id: cur.it.id, quantity: cur.qty, options: cur.sel});
    $("#addBtn").innerHTML = `Agregar · ${money(amt)}`;
  }
  $("#qMinus").onclick = () => { if (cur && cur.qty > 1) { cur.qty--; refreshItem(); } };
  $("#qPlus").onclick = () => { if (cur && cur.qty < 50) { cur.qty++; refreshItem(); } };
  $("#addBtn").onclick = () => {
    const miss = missingGroup();
    if (miss) { $("#itemErr").textContent = `Elige una opción en «${miss.name}»`; $(`.grp[data-g="${CSS.escape(miss.id)}"]`).scrollIntoView({behavior: "smooth", block: "center"}); return; }
    const options = {}; for (const [k, v] of Object.entries(cur.sel)) if (v.length) options[k] = v;
    const key = cur.it.id + "|" + JSON.stringify(Object.keys(options).sort().map((k) => [k, [...options[k]].sort()]));
    const same = cart.find((l) => l.key === key);
    if (same) same.quantity = Math.min(50, same.quantity + cur.qty); else cart.push({key, item_id: cur.it.id, quantity: cur.qty, options});
    saveCart(); closeAll(); toast("Agregado al carrito");
  };

  // ------------------------------------------------------------------ carrito
  function saveCart() {
    cart = cart.filter((l) => menu.items[l.item_id] && menu.items[l.item_id].available);
    store.set(CART_KEY, cart); renderCartBadge(); if ($("#cartSheet").classList.contains("on")) renderCart();
  }
  function renderCartBadge() {
    const n = cart.reduce((s, l) => s + l.quantity, 0);
    $("#cartCount").textContent = n;
    $("#floatCart").classList.toggle("on", n > 0);
    $("#floatCartBtn").innerHTML = `<span>🛒 Ver carrito · ${n}</span><span class="pill">${money(subtotal())}</span>`;
  }
  function optionNames(l) {
    const it = menu.items[l.item_id]; const out = [];
    for (const g of it.groups) for (const oid of l.options[g.id] || []) { const o = g.options.find((x) => x.id === oid); if (o) out.push(o.name); }
    return out.join(", ");
  }
  function renderCart() {
    const checkout = view === "checkout";
    $("#cartView").hidden = checkout; $("#checkoutView").hidden = !checkout; $("#backBtn").hidden = !checkout;
    $("#cartTitle").textContent = checkout ? "Entrega y pago" : "Tu pedido";
    if (!checkout) {
      $("#cartView").innerHTML = cart.length ? cart.map((l, i) => `<div class="line"><div class="nm"><b>${esc(menu.items[l.item_id].name)}</b>
        ${optionNames(l) ? `<small>${esc(optionNames(l))}</small>` : ""}<small>${money(lineAmount(l))}</small></div>
        <div class="qty"><button type="button" data-d="-1" data-i="${i}">−</button><span>${l.quantity}</span><button type="button" data-d="1" data-i="${i}">+</button></div></div>`).join("")
        : `<div class="empty-cart"><div style="font-size:42px">🛍️</div><p>Tu carrito está vacío</p></div>`;
      $("#cartView").querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => {
        const l = cart[+b.dataset.i]; l.quantity += +b.dataset.d; if (l.quantity <= 0) cart.splice(+b.dataset.i, 1); saveCart();
      }));
    }
    renderFoot();
  }
  function renderFoot() {
    const sub = subtotal();
    if (view === "cart") {
      $("#cartFoot").innerHTML = `<div class="sum total"><span>Subtotal</span><span>${money(sub)}</span></div>
        <button class="btn" type="button" id="goCheckout" ${cart.length && S.accepting ? "" : "disabled"}>${S.accepting ? "Continuar" : "No recibimos pedidos ahora"}</button>`;
      $("#goCheckout").onclick = () => { view = "checkout"; renderCart(); initMap(); };
      return;
    }
    const q = quote && quote.subtotal === round2(sub) ? quote : null;
    const fee = q && q.delivery_fee != null ? money(q.delivery_fee) : "—";
    $("#cartFoot").innerHTML = `
      <div class="sum"><span>Productos</span><span>${money(sub)}</span></div>
      <div class="sum"><span>Envío</span><span>${fee}</span></div>
      ${q && q.service_fee ? `<div class="sum"><span>Tarifa de servicio</span><span>${money(q.service_fee)}</span></div>` : ""}
      <div class="sum total"><span>Total a pagar</span><span>${q && q.total != null ? money(q.total) : "—"}</span></div>
      <div class="err" id="orderErr"></div>
      <button class="btn" type="button" id="placeBtn" ${q && q.can_order ? "" : "disabled"}>${q && q.can_order ? `Confirmar pedido · ${money(q.total)}` : "Confirmar pedido"}</button>`;
    $("#placeBtn").onclick = placeOrder;
  }
  const round2 = (n) => Math.round(n * 100) / 100;
  $("#openCart").onclick = $("#floatCartBtn").onclick = () => { view = "cart"; renderCart(); open($("#cartSheet")); };
  $("#backBtn").onclick = () => { view = "cart"; renderCart(); };

  // ------------------------------------------------------------------ mapa y cotización
  function setQuoteBox(cls, text) { const b = $("#quoteBox"); b.className = `quote ${cls}`; $("#quoteText").innerHTML = text; }
  const onPointChange = debounce(async (lat, lon) => {
    point = {lat, lon};
    const seq = ++quoteSeq;
    setQuoteBox("loading", "Consultando el envío…");
    api("/geocode", {lat, lon}).then((g) => {
      if (seq !== quoteSeq || !g.ok || !g.address) return;
      point.geocoded = g.address;
      if (!addrDirty) $("#fAddr").value = g.address.short_text || g.address.text || "";
    }).catch(() => {});
    await refreshQuote(seq);
  }, 380);
  async function refreshQuote(seq = ++quoteSeq) {
    if (!point) return;
    try {
      const q = await api("/quote", {lat: point.lat, lon: point.lon, subtotal: round2(subtotal())});
      if (seq !== quoteSeq) return;
      quote = q;
      if (!q.available) setQuoteBox("bad", q.reason === "cerrado" ? "El restaurante está cerrado ahora" : "No llegamos a esta dirección. Mueve el pin o prueba otra.");
      else if (q.below_min_order) setQuoteBox("bad", `El pedido mínimo es <b>${money(q.min_order)}</b>`);
      else if (q.delivery_fee == null) setQuoteBox("bad", "No pudimos calcular el envío para este pedido");
      else setQuoteBox("ok", `Envío <b>${q.delivery_fee ? money(q.delivery_fee) : "gratis"}</b>${q.eta_min ? ` · llega en ${q.eta_min}–${q.eta_max} min` : ""}${q.surge ? " · alta demanda" : ""}`);
    } catch (e) {
      if (seq !== quoteSeq) return;
      quote = null; setQuoteBox("bad", esc(e.message));
    }
    renderFoot();
  }
  function loadScript(src) { return new Promise((ok, ko) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = ko; document.head.appendChild(s); }); }
  function loadCss(href) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); }
  async function initMap() {
    if (map) { setTimeout(() => map.resize && map.resize(), 50); if (point) refreshQuote(); return; }
    const saved = store.get(`point:${S.slug}`, null);
    const center = saved ? [saved.lat, saved.lon] : S.center;
    const pin = $("#pin");
    try {
      if (S.yandexKey) {
        await loadScript(`https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(S.yandexKey)}&lang=es_ES`);
        await new Promise((r) => ymaps.ready(r));
        const m = new ymaps.Map("map", {center, zoom: 16, controls: ["zoomControl"]}, {suppressMapOpenBlock: true});
        m.events.add("actionbegin", () => pin.classList.add("up"));
        m.events.add("actionend", () => { pin.classList.remove("up"); const [la, lo] = m.getCenter(); moved(la, lo); });
        map = {setCenter: (la, lo) => { m.setCenter([la, lo], 17); moved(la, lo); }, resize: () => m.container.fitToViewport()};
      } else {
        loadCss("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css");
        await loadScript("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js");
        const m = L.map("map", {zoomControl: true, attributionControl: true}).setView(center, 16);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 19, attribution: "© OpenStreetMap"}).addTo(m);
        m.on("movestart", () => pin.classList.add("up"));
        m.on("moveend", () => { pin.classList.remove("up"); const c = m.getCenter(); moved(c.lat, c.lng); });
        map = {setCenter: (la, lo) => m.setView([la, lo], 17), resize: () => m.invalidateSize()};
        setTimeout(() => m.invalidateSize(), 300);
      }
      moved(center[0], center[1]);
      if (!saved) locate(true);
    } catch (e) { setQuoteBox("bad", "No se pudo cargar el mapa. Revisa tu conexión."); }
  }
  function moved(la, lo) { addrDirty = false; store.set(`point:${S.slug}`, {lat: la, lon: lo}); onPointChange(la, lo); }
  function locate(silent) {
    if (!navigator.geolocation) return silent || toast("Tu navegador no permite ubicarte");
    navigator.geolocation.getCurrentPosition((p) => map && map.setCenter(p.coords.latitude, p.coords.longitude),
      () => silent || toast("No pudimos obtener tu ubicación"), {enableHighAccuracy: true, timeout: 8000});
  }
  $("#locateBtn").onclick = () => locate(false);
  $("#fAddr").addEventListener("input", () => (addrDirty = true));
  document.querySelectorAll('input[name="pay"]').forEach((r) => r.addEventListener("change", () => { $("#changeField").hidden = $('input[name="pay"]:checked').value !== "cash"; }));

  // ------------------------------------------------------------------ pedido
  const prof = store.get(PROFILE_KEY, {});
  $("#fFirst").value = prof.first_name || ""; $("#fLast").value = prof.last_name || ""; $("#fPhone").value = prof.phone || "";
  async function placeOrder() {
    const err = $("#orderErr"); err.textContent = "";
    const phone = $("#fPhone").value.replace(/\D/g, "");
    const checks = [[!point, "Marca tu ubicación en el mapa"], [$("#fAddr").value.trim().length < 3, "Escribe la dirección"],
      [$("#fRef").value.trim().length < 3, "Agrega número de casa o una referencia"], [$("#fFirst").value.trim().length < 2, "Escribe tu nombre"],
      [$("#fLast").value.trim().length < 2, "Escribe tu apellido"], [!/^[67]\d{7}$/.test(phone.replace(/^591/, "")), "Escribe un celular válido (8 dígitos)"]];
    const bad = checks.find((c) => c[0]); if (bad) { err.textContent = bad[1]; return; }
    const method = $('input[name="pay"]:checked').value;
    const body = {
      customer: {first_name: $("#fFirst").value.trim(), last_name: $("#fLast").value.trim(), phone},
      address: {lat: point.lat, lon: point.lon, text: $("#fAddr").value.trim(), reference: $("#fRef").value.trim(), comment: $("#fNote").value.trim(), geocoded: point.geocoded || null},
      payment: {method, change_for: method === "cash" && $("#fChange").value.trim() ? $("#fChange").value.replace(",", ".") : null},
      items: cart.map((l) => ({item_id: l.item_id, quantity: l.quantity, options: l.options})),
      expected_total: quote.total,
    };
    const btn = $("#placeBtn"); btn.disabled = true; btn.textContent = "Enviando…";
    try {
      const res = await api("/orders", body);
      store.set(PROFILE_KEY, body.customer); cart = []; store.set(CART_KEY, []);
      location.href = res.url;
    } catch (e) { err.textContent = e.message; btn.disabled = false; refreshQuote(); }
  }

  // ------------------------------------------------------------------ inicio
  window.addEventListener("message", (e) => {  // vista previa en vivo desde el portal
    if (e.origin !== location.origin || !e.data || e.data.type !== "theme") return;
    for (const [k, v] of Object.entries(e.data.vars || {})) document.body.style.setProperty(k, v);
  });
  api("/menu").then((m) => { menu = m; saveCart(); renderMenu(); renderCartBadge(); })
    .catch((e) => { $("#menu").innerHTML = `<p class="note" style="padding:40px 0">${esc(e.message)}</p>`; });
})();
