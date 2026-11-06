/* Cabina de operadores: pedido telefónico rápido (teclado) + guion de llamada con los datos rellenados.
   Precios, envío y total los calcula el servidor (/quote y /api/operador/orders), igual que en la web. */
(() => {
  const C = window.CAB;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const money = (n) => `${C.currency}${Number(n).toLocaleString("es-BO", {maximumFractionDigits: 2})}`;
  const round2 = (n) => Math.round(n * 100) / 100;
  const norm = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const debounce = (fn, ms) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };
  async function req(url, opt = {}) {
    const r = await fetch(url, opt.body !== undefined ? {method: opt.method || "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(opt.body)} : {method: opt.method || "GET"});
    let d = {}; try { d = await r.json(); } catch (e) {}
    if (r.status === 401 && url.startsWith("/api/operador")) { location.reload(); throw new Error("Sesión vencida"); }
    if (!r.ok) throw new Error(d.detail || "Algo salió mal");
    return d;
  }

  let me = null, call = null, timerH = null, step = 0, outcome = null;
  let partner = null, menu = null, lines = [], point = null, quote = null, quoteSeq = 0, map = null;
  let known = null, placed = null, hl = -1, results = [], pending = null;
  const done = {};

  // ------------------------------------------------------------------ llamada
  function setOff(off) { $(".cab").classList.toggle("off", off); $("#endCall").disabled = off; }
  async function newCall() {
    if (call && !confirm("Hay una llamada abierta. ¿Empezar otra sin registrarla?")) return;
    resetForm();
    call = await req("/api/operador/calls", {body: {slug: partner && partner.slug}});
    const t0 = Date.now(); $("#timer").hidden = false; clearInterval(timerH);
    timerH = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000); $("#timer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }, 1000);
    setOff(false); goStep(0); $("#partner").focus();
  }
  async function endCall() {
    if (!call) return;
    if (!outcome) { $("#endMsg").className = "msg bad"; $("#endMsg").textContent = "Elige el resultado de la llamada"; return; }
    try {
      await req(`/api/operador/calls/${call.id}`, {method: "PUT", body: {outcome, notes: $("#notes").value, phone: $("#phone").value,
        slug: partner && partner.slug, order_token: placed && placed.token}});
    } catch (e) { $("#endMsg").className = "msg bad"; $("#endMsg").textContent = e.message; return; }
    call = null; clearInterval(timerH); $("#timer").hidden = true; resetForm(); setOff(true); loadMine(); ping();
    $("#endMsg").className = "msg ok"; $("#endMsg").textContent = "Llamada registrada ✓";
  }
  function resetForm() {
    lines = []; quote = null; known = null; placed = null; outcome = null; pending = null;
    for (const id of ["phone", "first", "last", "addr", "ref", "note", "change", "notes", "q"]) $(`#${id}`).value = "";
    $("#known").innerHTML = ""; $("#optsBox").innerHTML = ""; $("#err").textContent = ""; $("#endMsg").textContent = "";
    $$("#outcomes button").forEach((b) => b.classList.remove("on"));
    $("input[name=pay][value=cash]").checked = true;
    $("#place").textContent = "Confirmar pedido"; renderLines(); refreshQuote(); update();
  }

  // ------------------------------------------------------------------ guion
  function partOfDay() { const h = new Date().getHours(); return h < 12 ? "Buenos días" : h < 19 ? "Buenas tardes" : "Buenas noches"; }
  function vars() {
    const sub = subtotal();
    const pay = $("input[name=pay]:checked").value === "qr" ? "con QR al mensajero" : "en efectivo";
    const q = quote && quote.subtotal === round2(sub) ? quote : quote;
    return {
      saludo: partOfDay(), restaurante: partner && partner.name, operador: me.operator.name,
      cliente: $("#first").value.trim() || null,
      direccion: (known && known.addresses[0] && `${known.addresses[0].text}, ${known.addresses[0].reference}`) || $("#addr").value.trim() || null,
      envio: q && q.delivery_fee != null ? money(q.delivery_fee) : null,
      rango: q && q.fee_range ? `${money(q.fee_range.min)} a ${money(q.fee_range.max)}` : null,
      subtotal: sub ? money(sub) : null,
      total: q && q.total != null && q.subtotal === round2(sub) && sub ? money(q.total) : null,
      eta: q && q.eta_min ? `${q.eta_min} a ${q.eta_max}` : null,
      pago: pay,
      resumen: lines.length ? lines.map((l) => `${l.quantity} ${l.label}`).join(", ") : null,
      numero: placed && placed.number,
    };
  }
  function fillSay(text) {
    const v = vars();
    return esc(text).replace(/\{([a-z_]+)\}/g, (m, k) => (k in v ? (v[k] ? `<mark>${esc(v[k])}</mark>` : `<span class="miss">[${esc(k)}]</span>`) : m));
  }
  function goStep(i) {
    const steps = me.script; step = Math.max(0, Math.min(steps.length - 1, i));
    renderScript();
    $$(".blk").forEach((b) => b.classList.toggle("cur", b.dataset.step === steps[step].id));
  }
  function renderScript() {
    const steps = me.script, s = steps[step];
    $("#stepList").innerHTML = steps.map((x, i) => `<li class="${i === step ? "on" : ""}${done[x.id] ? " done" : ""}"><button data-go="${i}">${done[x.id] ? "✓ " : ""}${esc(x.title)}</button></li>`).join("");
    $$("#stepList [data-go]").forEach((b) => (b.onclick = () => goStep(+b.dataset.go)));
    $("#stepN").textContent = `Paso ${step + 1} de ${steps.length}`;
    $("#say").innerHTML = fillSay(s.say);
    $("#tips").innerHTML = s.tips ? fillSay(s.tips) : "";
  }
  // Al entrar a un bloque del formulario, el guion salta a ese paso.
  $$(".blk").forEach((b) => b.addEventListener("focusin", () => {
    const i = me && me.script.findIndex((s) => s.id === b.dataset.step);
    if (i >= 0 && i !== step) goStep(i);
  }));

  // ------------------------------------------------------------------ restaurante y menú
  async function selectPartner(slug) {
    partner = me.partners.find((p) => p.slug === slug) || null;
    menu = null; lines = []; renderLines(); quote = null;
    if (!partner) return update();
    $("#partnerState").className = `pill ${partner.accepting_orders && partner.ready ? "ok" : "manual"}`;
    $("#partnerState").textContent = !partner.ready ? "sin configurar" : partner.accepting_orders ? "recibiendo" : "pausado";
    $("#partnerInfo").textContent = [partner.address, partner.hours].filter(Boolean).join(" · ");
    if (map && partner.center[0] != null && !point) map.setView(partner.center, 15);
    try { menu = await req(`/api/${partner.slug}/menu`); } catch (e) { $("#partnerInfo").textContent = e.message; }
    refreshQuote(); update();
  }

  // ------------------------------------------------------------------ cliente
  const lookup = debounce(async () => {
    const digits = $("#phone").value.replace(/\D/g, "");
    if (digits.length < 8) { known = null; $("#known").innerHTML = ""; return update(); }
    try { known = await req(`/api/operador/customer?phone=${encodeURIComponent(digits)}`); } catch (e) { known = null; $("#known").innerHTML = `<p class="msg bad">${esc(e.message)}</p>`; return update(); }
    if (!known.known) { $("#known").innerHTML = `<p class="muted small">Cliente nuevo.</p>`; return update(); }
    const c = known.customer;
    if (!$("#first").value) $("#first").value = c.first_name;
    if (!$("#last").value) $("#last").value = c.last_name;
    $("#known").innerHTML = `<div class="known"><b>Cliente conocido:</b> ${esc(c.first_name)} ${esc(c.last_name)} · ${known.orders.length} pedido(s)
      ${known.orders.slice(0, 2).map((o) => `<div class="small muted">${esc(new Date(o.created_at).toLocaleDateString("es-BO"))} · ${esc(o.partner)} · ${esc(o.items.join(", "))} · ${money(o.total)}</div>`).join("")}
      ${known.addresses.map((a, i) => `<div class="ad"><button class="btn sm" data-ad="${i}">Usar</button><span>${esc(a.text)} · <b>${esc(a.reference)}</b></span></div>`).join("")}</div>`;
    $$("[data-ad]").forEach((b) => (b.onclick = () => useAddress(known.addresses[+b.dataset.ad])));
    update();
  }, 250);
  function useAddress(a) {
    $("#addr").value = a.text; $("#ref").value = a.reference; $("#note").value = a.comment || "";
    point = {lat: a.lat, lon: a.lon}; addrFromMap = false;
    if (map) map.setView([a.lat, a.lon], 17); else refreshQuote();
  }

  // ------------------------------------------------------------------ mapa y cotización
  let addrFromMap = true;
  async function initMap() {
    const link = document.createElement("link"); link.rel = "stylesheet"; link.href = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"; document.head.appendChild(link);
    await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
    const c = (partner && partner.center[0] != null) ? partner.center : [-17.7833, -63.1821];
    map = L.map("map").setView(c, 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 19, attribution: "© OpenStreetMap"}).addTo(map);
    map.on("moveend", debounce(() => { const m = map.getCenter(); point = {lat: m.lat, lon: m.lng}; geocode(); refreshQuote(); }, 350));
  }
  async function geocode() {
    if (!partner || !point || !addrFromMap) { addrFromMap = true; return; }
    try { const g = await req(`/api/${partner.slug}/geocode`, {body: point}); if (g.ok && g.address) $("#addr").value = g.address.short_text || g.address.text; } catch (e) {}
    update();
  }
  async function refreshQuote() {
    const seq = ++quoteSeq;
    if (!partner || !point || !partner.ready) { quote = null; setQuote("", "Mueve el mapa hasta la puerta del cliente"); return update(); }
    try {
      const q = await req(`/api/${partner.slug}/quote`, {body: {lat: point.lat, lon: point.lon, subtotal: round2(subtotal())}});
      if (seq !== quoteSeq) return;
      quote = q;
      if (!q.available) setQuote("bad", q.reason === "fuera_de_zona" ? "Sin cobertura en esa dirección" : q.reason === "cerrado" ? "El restaurante está cerrado" : "No disponible");
      else setQuote("ok", `Envío <b>${money(q.delivery_fee)}</b>${q.fee_range ? ` (${esc(q.fee_range.label.toLowerCase())}: ${money(q.fee_range.min)}–${money(q.fee_range.max)})` : ""} · ${String(q.distance_km).replace(".", ",")} km${q.eta_min ? ` · llega en ${q.eta_min}–${q.eta_max} min` : ""}${q.surge ? " · alta demanda" : ""}${q.below_min_order ? ` · mínimo ${money(q.min_order)}` : ""}`);
    } catch (e) { if (seq === quoteSeq) { quote = null; setQuote("bad", esc(e.message)); } }
    update();
  }
  function setQuote(cls, html) { $("#quote").className = `quote ${cls}`; $("#quote").innerHTML = html; }

  // ------------------------------------------------------------------ platos
  const itemPrice = (it) => (it.promo_price != null ? it.promo_price : it.price);
  function lineAmount(l) {
    const it = menu && menu.items[l.item_id]; if (!it) return 0;
    let u = itemPrice(it);
    for (const g of it.groups) for (const oid of l.options[g.id] || []) { const o = g.options.find((x) => x.id === oid); if (o) u += o.price; }
    return u * l.quantity;
  }
  const subtotal = () => lines.reduce((s, l) => s + lineAmount(l), 0);
  function search() {
    const q = norm($("#q").value.trim());
    if (!menu || !q) { results = []; $("#results").classList.remove("on"); return; }
    const words = q.split(/\s+/);
    results = Object.values(menu.items).filter((it) => { const t = norm(`${it.name} ${it.description || ""}`); return words.every((w) => t.includes(w)); })
      .sort((a, b) => (b.available - a.available) || (norm(a.name).startsWith(q) ? -1 : 0) - (norm(b.name).startsWith(q) ? -1 : 0)).slice(0, 12);
    hl = results.length ? 0 : -1; renderResults();
  }
  function renderResults() {
    $("#results").innerHTML = results.map((it, i) => `<button type="button" data-r="${i}" class="${i === hl ? "hl" : ""}" ${it.available ? "" : "disabled"}><span>${esc(it.name)}${it.groups.length ? " <small>· con opciones</small>" : ""}${it.available ? "" : " <small>· agotado</small>"}</span><b>${money(itemPrice(it))}</b></button>`).join("")
      || `<div class="muted small" style="padding:10px">Sin resultados</div>`;
    $("#results").classList.add("on");
    $$("#results [data-r]").forEach((b) => (b.onmousedown = (e) => { e.preventDefault(); pick(results[+b.dataset.r]); }));
  }
  function pick(it) {
    if (!it || !it.available) return;
    $("#results").classList.remove("on"); $("#q").value = "";
    if (!it.groups.length) return addLine(it, {});
    // Opciones: las obligatorias de una sola elección vienen marcadas con la primera.
    pending = {it, options: {}};
    for (const g of it.groups) if ((g.required || g.min) && (g.max === 1)) pending.options[g.id] = [g.options[0].id];
    renderOpts();
  }
  function renderOpts() {
    if (!pending) { $("#optsBox").innerHTML = ""; return; }
    const it = pending.it;
    $("#optsBox").innerHTML = `<div class="opts"><b>${esc(it.name)}</b>${it.groups.map((g) => {
      const single = g.max === 1, need = Math.max(g.min, g.required ? 1 : 0);
      return `<div class="g"><b>${esc(g.name)}${need ? ` <small class="muted">· elige ${need}${g.max > 1 ? ` (máx. ${g.max})` : ""}</small>` : ` <small class="muted">· opcional</small>`}</b>${g.options.map((o) => `<label><input type="${single ? "radio" : "checkbox"}" name="g${esc(g.id)}" data-g="${esc(g.id)}" value="${esc(o.id)}" ${(pending.options[g.id] || []).includes(o.id) ? "checked" : ""}> ${esc(o.name)}${o.price ? ` +${money(o.price)}` : ""}</label>`).join("")}</div>`;
    }).join("")}<div class="row"><button class="btn sm" id="optAdd">Agregar (Enter)</button><button class="btn ghost sm" id="optCancel">Cancelar (Esc)</button><span class="msg bad" id="optErr"></span></div></div>`;
    $$("#optsBox [data-g]").forEach((inp) => (inp.onchange = () => {
      pending.options[inp.dataset.g] = $$(`#optsBox [data-g="${CSS.escape(inp.dataset.g)}"]:checked`).map((x) => x.value);
    }));
    $("#optAdd").onclick = confirmOpts; $("#optCancel").onclick = () => { pending = null; renderOpts(); $("#q").focus(); };
    const first = $("#optsBox input"); if (first) first.focus();
  }
  function confirmOpts() {
    const it = pending.it;
    for (const g of it.groups) {
      const n = (pending.options[g.id] || []).length, need = Math.max(g.min, g.required ? 1 : 0);
      if (n < need) { $("#optErr").textContent = `Elige ${need} en «${g.name}»`; return; }
      if (g.max && n > g.max) { $("#optErr").textContent = `Máximo ${g.max} en «${g.name}»`; return; }
    }
    addLine(it, pending.options); pending = null; renderOpts();
  }
  function addLine(it, options) {
    const key = JSON.stringify([it.id, options]);
    const same = lines.find((l) => JSON.stringify([l.item_id, l.options]) === key);
    if (same) same.quantity += 1;
    else {
      const names = it.groups.flatMap((g) => (options[g.id] || []).map((oid) => (g.options.find((o) => o.id === oid) || {}).name)).filter(Boolean);
      lines.push({item_id: it.id, quantity: 1, options, label: it.name, extra: names.join(", ")});
    }
    renderLines(); refreshQuote(); $("#q").focus();
  }
  function renderLines() {
    $("#lines").innerHTML = lines.map((l, i) => `<div class="line"><div class="qty"><button data-m="${i}">−</button><b>${l.quantity}</b><button data-p="${i}">+</button></div>
      <div class="nm">${esc(l.label)}${l.extra ? `<small>${esc(l.extra)}</small>` : ""}</div><b>${money(lineAmount(l))}</b></div>`).join("") || `<p class="muted small">Sin productos todavía.</p>`;
    $$("#lines [data-m]").forEach((b) => (b.onclick = () => { const l = lines[+b.dataset.m]; l.quantity -= 1; if (l.quantity < 1) lines.splice(+b.dataset.m, 1); renderLines(); refreshQuote(); }));
    $$("#lines [data-p]").forEach((b) => (b.onclick = () => { lines[+b.dataset.p].quantity += 1; renderLines(); refreshQuote(); }));
    update();
  }

  // ------------------------------------------------------------------ totales y pedido
  function phoneOk() { return /^[67]\d{7}$/.test($("#phone").value.replace(/\D/g, "").replace(/^591(?=\d{8}$)/, "")); }
  function update() {
    if (!me) return;
    const sub = round2(subtotal());
    const q = quote && quote.subtotal === sub ? quote : null;
    done.saludo = !!partner;
    done.cliente = phoneOk() && $("#first").value.trim().length >= 2 && $("#last").value.trim().length >= 2;
    done.direccion = !!(quote && quote.available) && $("#ref").value.trim().length >= 3 && $("#addr").value.trim().length >= 3;
    done.cobertura = !!(quote && quote.available);
    done.pedido = lines.length > 0;
    done.pago = done.pedido;
    done.confirmacion = !!placed; done.despedida = !!placed && !!outcome;
    $$(".blk").forEach((b) => b.classList.toggle("done", !!done[b.dataset.step]));
    $("#sums").innerHTML = `<div class="r"><span>Subtotal</span><span>${money(sub)}</span></div>
      <div class="r"><span>Envío</span><span>${q && q.delivery_fee != null ? money(q.delivery_fee) : "—"}</span></div>
      <div class="r t"><span>Total a cobrar</span><span>${q && q.total != null && sub ? money(q.total) : "—"}</span></div>`;
    const ready = !!call && !placed && done.cliente && done.direccion && done.pedido && q && q.can_order;
    $("#place").disabled = !ready;
    $("#changeF").hidden = $("input[name=pay]:checked").value !== "cash";
    if (me.script[step]) renderScript();
  }
  async function placeOrder() {
    if ($("#place").disabled) return;
    const btn = $("#place"); btn.disabled = true; $("#err").textContent = "";
    const body = {
      slug: partner.slug,
      customer: {first_name: $("#first").value, last_name: $("#last").value, phone: $("#phone").value},
      address: {lat: point.lat, lon: point.lon, text: $("#addr").value, reference: $("#ref").value, comment: $("#note").value},
      payment: {method: $("input[name=pay]:checked").value, change_for: $("#change").value || null},
      items: lines.map((l) => ({item_id: l.item_id, quantity: l.quantity, options: l.options})),
      expected_total: quote.total,
    };
    try {
      const r = await req("/api/operador/orders", {body});
      placed = r; outcome = "pedido";
      $$("#outcomes button").forEach((b) => b.classList.toggle("on", b.dataset.o === "pedido"));
      btn.textContent = `✓ Pedido #${r.number} creado`;
      goStep(me.script.findIndex((s) => s.id === "despedida") >= 0 ? me.script.findIndex((s) => s.id === "despedida") : me.script.length - 1);
      loadMine(); ping();
    } catch (e) { $("#err").textContent = e.message; btn.disabled = false; refreshQuote(); }
    update();
  }

  // Latido cada minuto: cuenta las horas conectado y trae los números del día.
  const hrs = (h) => { const m = Math.round((h || 0) * 60); return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`; };
  async function ping() {
    try {
      const r = await req("/api/operador/ping", {body: {}}), t = r.today; if (!t) { $("#myday").innerHTML = `<p class="muted small">Entraste como administrador: no se cuentan horas.</p>`; return; }
      $("#myday").innerHTML = [[hrs(t.hours_online), "Horas conectado"], [t.calls, "Llamadas"], [t.orders, "Pedidos"], [money(t.sales), "Ventas"]].map(([v, l]) => `<div><b>${esc(v)}</b><span>${l}</span></div>`).join("");
      $("#mydayIn").textContent = t.first_in ? `Entrada: ${new Date(t.first_in).toLocaleTimeString("es-BO", {hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/La_Paz"})}` : "";
    } catch (e) {}
  }
  async function loadMine() {
    try {
      const rows = await req("/api/operador/orders");
      $("#mine").innerHTML = rows.slice(0, 10).map((o) => `<div class="o"><b class="mono">#${esc(o.number)}</b> · ${esc(o.partner)} · ${money(o.total)} <span class="pill ${esc(o.status)}">${esc(o.status)}</span><br><span class="muted">${esc(o.customer)} · ${esc(o.phone)}</span></div>`).join("") || `<p class="muted small">Todavía no cargaste pedidos.</p>`;
    } catch (e) {}
  }

  // ------------------------------------------------------------------ eventos
  $("#newCall").onclick = newCall; $("#endCall").onclick = endCall;
  $("#logout").onclick = async () => { await req("/api/operador/logout", {body: {}}); location.reload(); };
  $("#prev").onclick = () => goStep(step - 1); $("#next").onclick = () => goStep(step + 1);
  $("#partner").onchange = (e) => selectPartner(e.target.value);
  $("#phone").addEventListener("input", () => { lookup(); update(); });
  for (const id of ["first", "last", "addr", "ref", "note", "change"]) $(`#${id}`).addEventListener("input", update);
  $("#addr").addEventListener("input", () => (addrFromMap = false));
  $$("input[name=pay]").forEach((r) => (r.onchange = update));
  $("#place").onclick = placeOrder;
  $("#q").addEventListener("input", search);
  $("#q").addEventListener("blur", () => setTimeout(() => $("#results").classList.remove("on"), 120));
  $("#q").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (!results.length) return; hl = (hl + (e.key === "ArrowDown" ? 1 : -1) + results.length) % results.length; renderResults(); }
    else if (e.key === "Enter") { e.preventDefault(); pick(results[hl]); }
    else if (e.key === "Escape") { $("#results").classList.remove("on"); }
  });
  $("#optsBox").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmOpts(); }
    else if (e.key === "Escape") { pending = null; renderOpts(); $("#q").focus(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "F2") { e.preventDefault(); newCall(); }
    else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); goStep(step + 1); }
    else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); goStep(step - 1); }
    else if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); placeOrder(); }
    else if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) { e.preventDefault(); $("#q").focus(); }
  });

  // ------------------------------------------------------------------ inicio
  req("/api/operador/me").then(async (d) => {
    me = d;
    $("#opName").textContent = d.operator.name;
    $("#partner").innerHTML = `<option value="">Elige el restaurante…</option>` + d.partners.map((p) => `<option value="${esc(p.slug)}">${esc(p.name)}${p.accepting_orders ? "" : " (pausado)"}</option>`).join("");
    $("#outcomes").innerHTML = Object.entries(d.outcomes).map(([k, v]) => `<button type="button" data-o="${esc(k)}">${esc(v)}</button>`).join("");
    $$("#outcomes button").forEach((b) => (b.onclick = () => { outcome = b.dataset.o; $$("#outcomes button").forEach((x) => x.classList.toggle("on", x === b)); update(); }));
    if (d.partners.length === 1) { $("#partner").value = d.partners[0].slug; await selectPartner(d.partners[0].slug); }
    setOff(true); goStep(0); update(); loadMine(); ping(); setInterval(ping, 60000);
    initMap().catch(() => setQuote("bad", "No se pudo cargar el mapa"));
  });
})();
