/* Tienda: menú, carrito, checkout con mapa (pin fijo al centro) y cotización en vivo con Yango.
   Movimiento: aparición al hacer scroll, onda al pulsar, foco que sigue al ratón y vuelo al carrito. */
(() => {
  const S = window.STORE;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const icon = (n) => `<svg class="ic" aria-hidden="true"><use href="/static/icons.svg?v=${S.v}#${n}"></use></svg>`;
  const money = (n) => `${S.currency}${Number(n).toLocaleString("es-BO", {maximumFractionDigits: 2})}`;
  const round2 = (n) => Math.round(n * 100) / 100;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  const CART_KEY = `cart:${S.slug}`, PROFILE_KEY = "customer", POINT_KEY = `point:${S.slug}`;

  let menu = null;
  let cart = store.get(CART_KEY, []);
  let view = "cart";
  let point = null, quote = null, quoteSeq = 0, addrDirty = false, map = null;
  let branch = null;  // sucursal que atiende la dirección del cliente (la asigna Yango)
  const tr = (id) => (menu && menu.alias && menu.alias[id]) || id;  // ids configurados (principal) → esta sucursal

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
  function closeAll() {
    $$(".sheet.on").forEach((e) => e.classList.remove("on")); $("#scrim").classList.remove("on"); document.body.style.overflow = "";
    if ($("#ttFrame").src) $("#ttFrame").removeAttribute("src");  // detiene el video
  }
  function showErr(el, msg) { el.textContent = msg; el.classList.remove("shake"); void el.offsetWidth; if (msg) el.classList.add("shake"); }
  $("#scrim").onclick = closeAll;
  $$("[data-close]").forEach((b) => (b.onclick = closeAll));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAll(); });

  // Cabecera: sombra y nombre al bajar.
  const top = $("#top");
  const onScroll = () => top.classList.toggle("scrolled", window.scrollY > 140);
  window.addEventListener("scroll", onScroll, {passive: true}); onScroll();

  // Onda al pulsar botones.
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest(".btn, .cart-btn, .add");
    if (!b || reduced) return;
    const r = b.getBoundingClientRect(), s = document.createElement("span");
    s.className = "ripple"; s.style.left = `${e.clientX - r.left}px`; s.style.top = `${e.clientY - r.top}px`;
    b.appendChild(s); setTimeout(() => s.remove(), 650);
  });
  // Foco que sigue al ratón en las tarjetas.
  document.addEventListener("pointermove", (e) => {
    const c = e.target.closest && e.target.closest(".card"); if (!c) return;
    const r = c.getBoundingClientRect();
    c.style.setProperty("--mx", `${e.clientX - r.left}px`); c.style.setProperty("--my", `${e.clientY - r.top}px`);
  }, {passive: true});
  // Aparición al hacer scroll (con escalonado por fila).
  const io = !reduced && "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
  }, {rootMargin: "0px 0px -8% 0px", threshold: 0.05}) : null;
  function reveal(root) {
    $$(".reveal", root).forEach((el, i) => { el.style.setProperty("--i", i % 8); io ? io.observe(el) : el.classList.add("in"); });
  }

  // ------------------------------------------------------------------ menú
  const itemPrice = (it) => (it.promo_price != null ? it.promo_price : it.price);
  function lineAmount(l) {
    const it = menu.items[l.item_id]; if (!it) return 0;
    let unit = itemPrice(it);
    for (const g of it.groups) for (const oid of l.options[g.id] || []) { const o = g.options.find((x) => x.id === oid); if (o) unit += o.price; }
    return unit * l.quantity;
  }
  const subtotal = () => cart.reduce((s, l) => s + lineAmount(l), 0);
  const qtyOf = (id) => cart.filter((l) => l.item_id === id).reduce((s, l) => s + l.quantity, 0);

  function renderMenu() {
    const cats = menu.categories.map((c) => ({...c, items: c.items.map((id) => menu.items[id]).filter(Boolean)})).filter((c) => c.items.length);
    $("#cats").innerHTML = cats.map((c, i) => `<a href="#c${i}">${esc(c.name)}</a>`).join("");
    // La primera categoría con fotos va como carrusel de destacados.
    const railIdx = cats.findIndex((c) => c.items.filter((it) => it.image).length >= 3);
    $("#menu").innerHTML = cats.map((c, i) => `
      <section class="section" id="c${i}"><h2 class="reveal">${esc(c.name)}<small>${c.items.length}</small></h2>
        <div class="${i === railIdx ? "rail" : "grid"}">${c.items.map(card).join("")}</div></section>`).join("")
      || `<p class="note" style="padding:40px 0">El menú está vacío por ahora.</p>`;
    $$(".card[data-id]").forEach((el) => (el.onclick = () => openItem(el.dataset.id)));
    reveal($("#menu")); spyCategories();
    if ($("#cover") && !$("#cover > img")) coverFromMenu();
  }
  function card(it) {
    const promo = it.promo_price != null && it.promo_price < it.price;
    const q = qtyOf(it.id);
    return `<button type="button" class="card reveal${it.available ? "" : " off"}" data-id="${esc(it.id)}" ${it.available ? "" : "disabled"}>
      <div class="bd"><h3>${esc(it.name)}</h3>${it.description ? `<div class="ds">${esc(it.description)}</div>` : ""}
        <div class="ft"><span class="price${promo ? " promo" : ""}">${money(itemPrice(it))}${promo ? `<s>${money(it.price)}</s>` : ""}</span>
        ${promo ? `<span class="badge">-${Math.round((1 - it.promo_price / it.price) * 100)}%</span>` : ""}${it.available ? "" : `<span class="badge">Agotado</span>`}</div></div>
      <div class="ph${it.image ? "" : " empty"}">${it.image ? `<img loading="lazy" src="${esc(it.image)}" alt="">` : esc(it.name[0] || "")}
        ${q ? `<span class="qbadge">${q}</span>` : ""}${it.available ? `<span class="add" aria-hidden="true">+</span>` : ""}</div></button>`;
  }
  function refreshBadges() {
    $$(".card[data-id]").forEach((c) => {
      const q = qtyOf(c.dataset.id), b = $(".qbadge", c);
      if (q && b) b.textContent = q;
      else if (q) $(".ph", c).insertAdjacentHTML("afterbegin", `<span class="qbadge">${q}</span>`);
      else if (b) b.remove();
    });
  }
  function coverFromMenu() {
    // Sin portada propia: collage con fotos del menú.
    const imgs = Object.values(menu.items).filter((x) => x.image && x.available).slice(0, 5);
    if (!imgs.length) return;
    $("#cover").insertAdjacentHTML("afterbegin", `<div class="collage">${imgs.map((x, i) => `<img src="${esc(x.image)}" alt="" style="animation-delay:${i * 90}ms">`).join("")}</div>`);
  }
  function spyCategories() {
    const links = $$("#cats a");
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === `#${e.target.id}`));
        const on = links.find((a) => a.classList.contains("on"));
        if (on) on.parentElement.scrollTo({left: on.offsetLeft - 16, behavior: reduced ? "auto" : "smooth"});
      });
    }, {rootMargin: "-130px 0px -65% 0px"});
    $$(".section").forEach((s) => obs.observe(s));
  }
  const norm = (t) => String(t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  $("#search").addEventListener("input", debounce(() => {
    if (!menu) return;
    const q = norm($("#search").value.trim());
    $$(".section").forEach((sec) => {
      let any = false;
      $$(".card[data-id]", sec).forEach((c) => {
        const it = menu.items[c.dataset.id], hit = !q || norm(`${it.name} ${it.description}`).includes(q);
        c.classList.toggle("hidden-by-search", !hit); if (hit) c.classList.add("in"); any = any || hit;
      });
      sec.classList.toggle("hidden-by-search", !any);
    });
  }, 120));

  // ------------------------------------------------------------------ producto
  let cur = null;
  function openItem(id) {
    const it = menu.items[id]; if (!it || !it.available) return;
    cur = {it, qty: 1, sel: {}};
    $("#itemBody").innerHTML = `${it.image ? `<div class="item-img"><img src="${esc(it.image)}" alt=""></div>` : `<div style="height:56px"></div>`}
      <div class="item-top"><h2>${esc(it.name)}</h2><span class="price">${money(itemPrice(it))}</span></div>
      ${it.description ? `<p class="item-ds">${esc(it.description)}</p>` : ""}
      ${it.groups.map((g) => {
        const need = Math.max(g.min, g.required ? 1 : 0), single = g.max === 1;
        const rule = single ? (need ? "Elige 1" : "Opcional") : g.max ? `${need ? `Mín. ${need} · ` : ""}Máx. ${g.max}` : need ? `Mín. ${need}` : "Opcional";
        return `<div class="grp" data-g="${esc(g.id)}"><div class="grp-h"><b>${esc(g.name)}</b><span class="${need ? "req" : ""}" data-rule="${esc(rule)}">${rule}</span></div>
          <div class="chips-opts">${g.options.map((o) => `<label class="optc"><input type="${single && need ? "radio" : "checkbox"}" name="g${esc(g.id)}" value="${esc(o.id)}">
            <span>${esc(o.name)}</span>${o.price ? `<span class="pr">+${money(o.price)}</span>` : ""}</label>`).join("")}</div></div>`;
      }).join("")}`;
    $$(".grp", $("#itemBody")).forEach((gEl) => gEl.addEventListener("change", (e) => {
      const g = it.groups.find((x) => x.id === gEl.dataset.g);
      let checked = $$("input:checked", gEl);
      if (g.max === 1 && e.target.type === "checkbox" && e.target.checked) { checked.forEach((c) => c !== e.target && (c.checked = false)); checked = [e.target]; }
      if (g.max > 1 && checked.length > g.max) { e.target.checked = false; toast(`Máximo ${g.max}`); checked = checked.filter((c) => c !== e.target); }
      cur.sel[g.id] = checked.map((c) => c.value);
      const need = Math.max(g.min, g.required ? 1 : 0), tag = $(".grp-h span", gEl);
      if (need) { const ok = cur.sel[g.id].length >= need; tag.classList.toggle("done", ok); tag.classList.toggle("req", !ok); tag.innerHTML = ok ? `${icon("check")}Listo` : esc(tag.dataset.rule); }
      refreshItem();
    }));
    $("#itemBody").insertAdjacentHTML("beforeend", `<div class="ups" id="itemUps"></div>`);
    loadUpsell([it.id], "item", $("#itemUps"));
    showErr($("#itemErr"), "");
    if (!$("#itemSheet").classList.contains("on")) open($("#itemSheet"));
    refreshItem(); $("#itemBody").scrollTop = 0;
  }
  function missingGroup() {
    for (const g of cur.it.groups) { const need = Math.max(g.min, g.required ? 1 : 0); if ((cur.sel[g.id] || []).length < need) return g; }
    return null;
  }
  function refreshItem() {
    const q = $("#qVal"); q.textContent = cur.qty; q.classList.remove("tick"); void q.offsetWidth; q.classList.add("tick");
    $("#addBtn").innerHTML = `Agregar · ${money(lineAmount({item_id: cur.it.id, quantity: cur.qty, options: cur.sel}))}`;
  }
  $("#qMinus").onclick = () => { if (cur && cur.qty > 1) { cur.qty--; refreshItem(); } };
  $("#qPlus").onclick = () => { if (cur && cur.qty < 50) { cur.qty++; refreshItem(); } };
  $("#addBtn").onclick = () => {
    const miss = missingGroup();
    if (miss) { showErr($("#itemErr"), `Elige una opción en «${miss.name}»`); $(`.grp[data-g="${CSS.escape(miss.id)}"]`).scrollIntoView({behavior: "smooth", block: "center"}); return; }
    const options = {}; for (const [k, v] of Object.entries(cur.sel)) if (v.length) options[k] = v;
    const key = cur.it.id + "|" + JSON.stringify(Object.keys(options).sort().map((k) => [k, [...options[k]].sort()]));
    const same = cart.find((l) => l.key === key);
    if (same) same.quantity = Math.min(50, same.quantity + cur.qty); else cart.push({key, item_id: cur.it.id, quantity: cur.qty, options});
    flyToCart($(".item-img img") || $("#addBtn"), cur.it.image);
    saveCart(); closeAll(); toast(`${cur.it.name} agregado`);
  };
  function flyToCart(fromEl, img) {
    const target = window.innerWidth <= 640 && $("#floatCart").classList.contains("on") ? $("#floatCartBtn") : $("#openCart");
    const a = fromEl.getBoundingClientRect(), b = target.getBoundingClientRect();
    const bump = () => { target.classList.remove("bump"); void target.offsetWidth; target.classList.add("bump"); };
    if (reduced) return bump();
    const f = document.createElement(img ? "img" : "div"); f.className = "fly"; if (img) f.src = img;
    f.style.left = `${a.left + a.width / 2 - 23}px`; f.style.top = `${a.top + a.height / 2 - 23}px`;
    document.body.appendChild(f);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      f.style.transform = `translate(${b.left + b.width / 2 - (a.left + a.width / 2)}px, ${b.top + b.height / 2 - (a.top + a.height / 2)}px) scale(.35)`;
      f.style.opacity = ".6";
    }));
    setTimeout(() => { f.remove(); bump(); }, 720);
  }

  // ------------------------------------------------------------------ upselling («otras personas lo combinaron con»)
  const needsChoice = (it) => it.groups.some((g) => Math.max(g.min, g.required ? 1 : 0) > 0);
  const upsCache = {};
  async function loadUpsell(ids, context, box) {
    const key = `${context}:${[...new Set(ids)].sort().join(",")}`;
    const b = branch ? `&branch=${encodeURIComponent(branch.slug)}` : "";
    try { upsCache[key + b] = upsCache[key + b] || await api(`/upsell?context=${context}&items=${encodeURIComponent([...new Set(ids)].join(","))}${b}`); }
    catch (e) { return; }
    const recs = upsCache[key + b].items.map((r) => ({...r, it: menu.items[r.item_id]})).filter((r) => r.it && r.it.available);
    if (!recs.length || !box.isConnected) return;
    const drinks = recs.every((r) => r.reason === "bebida");
    const title = context === "cart" ? (drinks ? "¿Algo para tomar?" : "Otras personas también pidieron") : (drinks ? "Acompáñalo con una bebida" : "Otras personas lo combinaron con");
    box.innerHTML = `<h4>${icon(drinks ? "cup-soda" : "sparkles")}${title}</h4><div class="ups-list">${recs.map((r) => `
      <button type="button" class="ups-it" data-ups="${esc(r.it.id)}">
        ${r.it.image ? `<img loading="lazy" src="${esc(r.it.image)}" alt="">` : `<span class="noimg">${esc(r.it.name[0] || "")}</span>`}
        <span class="nm">${esc(r.it.name)}<b>${money(itemPrice(r.it))}</b></span>
        <span class="ups-add" aria-label="Agregar ${esc(r.it.name)}">${icon("plus")}</span></button>`).join("")}</div>`;
    $$("[data-ups]", box).forEach((b) => (b.onclick = () => quickAdd(b.dataset.ups, b)));
  }
  function quickAdd(id, el) {
    const it = menu.items[id]; if (!it) return;
    if (needsChoice(it)) return openItem(id);  // tiene opciones obligatorias: se eligen en su ficha
    const key = id + "|[]", same = cart.find((l) => l.key === key);
    if (same) same.quantity = Math.min(50, same.quantity + 1); else cart.push({key, item_id: id, quantity: 1, options: {}});
    flyToCart($("img", el) || el, it.image); saveCart(); toast(`${it.name} agregado`);
    if (view === "cart" && $("#cartSheet").classList.contains("on")) renderCart();
  }

  // ------------------------------------------------------------------ TikTok: videos elegidos por el restaurante con su producto
  // «Más vendidos»: lo calcula el servidor (pedidos de la web o elegidos a mano en el admin).
  function renderBest() {
    const its = (S.bestsellers || []).map((id) => menu.items[tr(id)]).filter((it) => it && it.available);
    $("#best").hidden = its.length < 2;
    if (its.length < 2) return;
    $("#bestRail").innerHTML = its.map(card).join("");
    $$(".card[data-id]", $("#bestRail")).forEach((el) => (el.onclick = () => openItem(el.dataset.id)));
    reveal($("#best"));
  }
  const videoItems = (v) => (v.item_ids || []).map((id) => menu.items[tr(id)]).filter((it) => it && it.available);
  function renderTikTok() {
    const vids = (S.tiktok || []).filter((v) => /^\d+$/.test(v.id));
    $("#tt").hidden = !vids.length;
    if (!vids.length) return;
    $("#ttRail").innerHTML = vids.map((v, i) => {
      const its = videoItems(v), it = its[0], poster = v.poster || (it && it.image) || "";
      return `<div class="tt-card reveal" data-v="${i}" role="button" tabindex="0" aria-label="Ver video${it ? ` de ${esc(it.name)}` : ""}">
        ${poster ? `<img loading="lazy" src="${esc(poster)}" alt="">` : ""}<span class="tt-play">${icon("play")}</span>
        ${it ? `<button type="button" class="tt-chip" data-item="${esc(it.id)}"><span>${esc(it.name)}</span><b>${money(itemPrice(it))}${its.length > 1 ? ` <small>+${its.length - 1} más</small>` : ""}</b></button>` : ""}</div>`;
    }).join("");
    $$(".tt-card", $("#ttRail")).forEach((c) => {
      const go = () => openVideo(vids[+c.dataset.v]);
      c.onclick = (e) => { if (!e.target.closest(".tt-chip")) go(); };
      c.onkeydown = (e) => { if (e.key === "Enter") go(); };
    });
    $$(".tt-chip", $("#ttRail")).forEach((b) => (b.onclick = () => { const v = vids[+b.closest(".tt-card").dataset.v]; videoItems(v).length > 1 ? openVideo(v) : openItem(b.dataset.item); }));
    reveal($("#tt"));
  }
  function openVideo(v) {
    const its = videoItems(v);
    $("#ttFrame").src = `https://www.tiktok.com/player/v1/${encodeURIComponent(v.id)}?autoplay=1&description=0&music_info=0&rel=0&native_context_menu=0`;
    const pedir = (id) => { closeAll(); setTimeout(() => openItem(id), 220); };
    // Tarjetas de vidrio flotando sobre el video (una por producto; con varios, se deslizan).
    $("#ttFoot").className = `tt-glass${its.length > 1 ? " many" : ""}`;
    $("#ttFoot").innerHTML = its.map((it) => `<button type="button" class="tt-glass-it" data-tt-it="${esc(it.id)}">
        ${it.image ? `<img src="${esc(it.image)}" alt="">` : `<span class="noimg">${esc(it.name[0] || "")}</span>`}
        <span class="nm">${esc(it.name)}<b>${money(itemPrice(it))}</b></span><span class="tt-go">Pedir</span></button>`).join("");
    $$("[data-tt-it]", $("#ttFoot")).forEach((b) => (b.onclick = () => pedir(b.dataset.ttIt)));
    open($("#ttSheet"));
  }

  // ------------------------------------------------------------------ carrito
  function saveCart() {
    cart = cart.filter((l) => menu.items[l.item_id] && menu.items[l.item_id].available);
    store.set(CART_KEY, cart); renderCartBadge(); refreshBadges();
    if ($("#cartSheet").classList.contains("on")) renderCart();
  }
  function renderCartBadge() {
    const n = cart.reduce((s, l) => s + l.quantity, 0);
    $("#cartCount").textContent = n;
    $("#floatCart").classList.toggle("on", n > 0);
    $("#floatCartBtn").innerHTML = `<span>Ver carrito · ${n}</span><span class="pill">${money(subtotal())}</span>`;
  }
  function optionNames(l) {
    const it = menu.items[l.item_id], out = [];
    for (const g of it.groups) for (const oid of l.options[g.id] || []) { const o = g.options.find((x) => x.id === oid); if (o) out.push(o.name); }
    return out.join(", ");
  }
  function renderCart() {
    const checkout = view === "checkout";
    $("#cartView").hidden = checkout; $("#checkoutView").hidden = !checkout; $("#backBtn").hidden = !checkout;
    $("#cartTitle").textContent = checkout ? "Entrega y pago" : "Tu pedido";
    if (!checkout) {
      $("#cartView").innerHTML = cart.length ? cart.map((l, i) => { const it = menu.items[l.item_id]; return `<div class="line" style="animation-delay:${i * 40}ms">
        ${it.image ? `<img src="${esc(it.image)}" alt="">` : `<span class="noimg"></span>`}
        <div class="nm"><b>${esc(it.name)}</b>${optionNames(l) ? `<small>${esc(optionNames(l))}</small>` : ""}<small>${money(lineAmount(l))}</small></div>
        <div class="qty"><button type="button" data-d="-1" data-i="${i}" aria-label="Menos">${icon(l.quantity === 1 ? "trash-2" : "minus")}</button><span>${l.quantity}</span><button type="button" data-d="1" data-i="${i}" aria-label="Más">${icon("plus")}</button></div></div>`; }).join("")
        : `<div class="empty-cart"><div class="ico">${icon("shopping-bag")}</div><p>Tu carrito está vacío</p><p class="note">Agrega algo rico del menú</p></div>`;
      if (cart.length) { $("#cartView").insertAdjacentHTML("beforeend", `<div class="ups cart-ups" id="cartUps"></div>`); loadUpsell(cart.map((l) => l.item_id), "cart", $("#cartUps")); }
      $$("[data-d]", $("#cartView")).forEach((b) => (b.onclick = () => {
        const l = cart[+b.dataset.i]; l.quantity += +b.dataset.d; if (l.quantity <= 0) cart.splice(+b.dataset.i, 1); saveCart();
      }));
    }
    renderFoot();
  }
  function renderFoot() {
    const sub = subtotal();
    if (view === "cart") {
      $("#cartFoot").innerHTML = `<div class="sum total"><span>Subtotal</span><span>${money(sub)}</span></div>
        <button class="btn" type="button" id="goCheckout" ${cart.length && S.accepting ? "" : "disabled"}>${S.accepting ? "Continuar al pago" : "No recibimos pedidos ahora"}</button>`;
      $("#goCheckout").onclick = () => { view = "checkout"; renderCart(); initMap(); };
      return;
    }
    const q = quote && quote.subtotal === round2(sub) ? quote : null;
    $("#cartFoot").innerHTML = `
      <div class="sum"><span>Productos</span><span>${money(sub)}</span></div>
      <div class="sum"><span>Envío</span><span>${q && q.delivery_fee != null ? (q.delivery_fee ? money(q.delivery_fee) : "Gratis") : "—"}</span></div>
      ${q && q.service_fee ? `<div class="sum"><span>Tarifa de servicio</span><span>${money(q.service_fee)}</span></div>` : ""}
      <div class="sum total"><span>Total a pagar</span><span>${q && q.total != null ? money(q.total) : "—"}</span></div>
      <div class="err" id="orderErr"></div>
      <button class="btn" type="button" id="placeBtn" ${q && q.can_order ? "" : "disabled"}>${q && q.can_order ? `Confirmar pedido · ${money(q.total)}` : "Confirmar pedido"}</button>`;
    $("#placeBtn").onclick = placeOrder;
  }
  const openCart = () => { view = "cart"; renderCart(); open($("#cartSheet")); };
  $("#openCart").onclick = openCart; $("#floatCartBtn").onclick = openCart;
  $("#backBtn").onclick = () => { view = "cart"; renderCart(); };
  $("#addrPill").onclick = () => openLoc(false);

  // ------------------------------------------------------------------ sucursal, mapa y cotización
  function setQuoteBox(cls, text) { $("#quoteBox").className = `quote ${cls}`; $("#quoteText").innerHTML = text; }
  const savePoint = (p) => store.set(POINT_KEY, {lat: p.lat, lon: p.lon, label: p.label || "", branch: branch && branch.slug});
  const onPointChange = debounce(async (lat, lon) => {
    point = {lat, lon};
    const seq = ++quoteSeq;
    setQuoteBox("loading", "Consultando el envío…");
    api("/geocode", {lat, lon}).then((g) => {
      if (seq !== quoteSeq || !g.ok || !g.address) return;
      point.geocoded = g.address;
      const txt = g.address.short_text || g.address.text || "";
      if (!addrDirty) $("#fAddr").value = txt;
      $("#addrPillText").textContent = txt || "Mi dirección";
      savePoint({lat, lon, label: txt});
    }).catch(() => {});
    await refreshQuote(seq);
  }, 380);
  async function refreshQuote(seq = ++quoteSeq) {
    if (!point) return;
    try {
      const q = await api("/quote", {lat: point.lat, lon: point.lon, subtotal: round2(subtotal()), branch: branch && branch.slug});
      if (seq !== quoteSeq) return;
      quote = q;
      if (!q.available) { setQuoteBox("bad", unavailableText(q)); otherBranch(seq); }
      else if (q.below_min_order) setQuoteBox("bad", `El pedido mínimo es <b>${money(q.min_order)}</b>`);
      else if (q.delivery_fee == null) setQuoteBox("bad", "No pudimos calcular el envío para este pedido");
      else setQuoteBox("ok", `Envío <b>${q.delivery_fee ? money(q.delivery_fee) : "gratis"}</b>${q.eta_min ? ` · llega en ${q.eta_min}–${q.eta_max} min` : ""}${q.surge ? " · alta demanda" : ""}`);
      if (q.available && q.eta_min) $("#metaEta").innerHTML = `${icon("bike")}<b>${q.eta_min}–${q.eta_max} min</b>`;
    } catch (e) {
      if (seq !== quoteSeq) return;
      quote = null; setQuoteBox("bad", esc(e.message));
    }
    renderFoot();
  }
  // El pin se movió a una zona que atiende otra sucursal: se ofrece cambiar (menú y precios de esa sucursal).
  async function otherBranch(seq) {
    try {
      const {branch: b} = await api(`/branch?lat=${point.lat}&lon=${point.lon}`);
      if (seq !== quoteSeq || !b || !branch || b.slug === branch.slug) return;
      setQuoteBox("bad", `Esta dirección la atiende <b>${esc(b.name)}</b>. <button type="button" class="linkbtn" id="swBranch">Cambiar de sucursal</button>`);
      $("#swBranch").onclick = async () => {
        setQuoteBox("loading", "Cargando el menú de esa sucursal…");
        try { await loadMenu(b.slug); savePoint({...point, label: $("#fAddr").value}); } catch (e) { toast(e.message); }
        refreshQuote();
      };
    } catch (e) {}
  }
  function unavailableText(q) {
    if (q.reason === "zona_cerrada") return "Por alta demanda no llegamos a esta zona ahora. Prueba más tarde.";
    if (q.reason === "cerrado") {
      const t = q.opens_at && new Date(q.opens_at);
      return `El restaurante está cerrado ahora${t && !isNaN(t) ? ` · abre a las ${t.toLocaleTimeString("es-BO", {hour: "2-digit", minute: "2-digit"})}` : ""}`;
    }
    return "No llegamos a esta dirección. Mueve el pin o prueba otra.";
  }
  function loadScript(src) { return new Promise((ok, ko) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = ko; document.head.appendChild(s); }); }
  function loadCss(href) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); }
  const primaryColor = () => getComputedStyle(document.body).getPropertyValue("--primary").trim() || "#E8402F";
  // Mapa de Yandex (los mismos datos que usan Yango y Yango Maps: calles y números de casa de Bolivia).
  // La clave YANDEX_MAPS_API_KEY es opcional; sin ella también carga. Leaflet/OSM queda solo de respaldo.
  async function yandexMap(id, center, lift, onMoved) {
    if (!window.ymaps) {
      const key = S.yandexKey ? `apikey=${encodeURIComponent(S.yandexKey)}&` : "";
      await loadScript(`https://api-maps.yandex.ru/2.1/?${key}lang=${encodeURIComponent(S.mapLang || "es_ES")}`);
    }
    await new Promise((ok, ko) => { ymaps.ready(ok, ko); setTimeout(() => ko(new Error("ymaps timeout")), 12000); });
    const m = new ymaps.Map(id, {center, zoom: 17, controls: ["zoomControl"]},
      {suppressMapOpenBlock: true, yandexMapDisablePoiInteractivity: true, zoomControlSize: "small", zoomControlPosition: {left: 10, top: 10}});
    let mark = null;
    m.events.add("actionbegin", () => lift(true));
    m.events.add("actionend", () => { lift(false); const [la, lo] = m.getCenter(); onMoved(la, lo); });
    return {setCenter: (la, lo) => m.setCenter([la, lo], 17, {duration: 300}), resize: () => m.container.fitToViewport(),
      setBranch: (b) => {
        if (mark) m.geoObjects.remove(mark);
        mark = b && b.lat != null ? new ymaps.Placemark([b.lat, b.lon], {hintContent: b.name, iconCaption: b.name}, {preset: "islands#foodCircleIcon", iconColor: primaryColor()}) : null;
        if (mark) m.geoObjects.add(mark);
      }};
  }
  async function leafletMap(id, center, lift, onMoved) {
    if (!window.L) {
      loadCss("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css");
      await loadScript("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js");
    }
    $(`#${id}`).innerHTML = "";
    const m = L.map(id, {zoomControl: true}).setView(center, 16);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {maxZoom: 19, subdomains: "abcd", attribution: "© OpenStreetMap © CARTO"}).addTo(m);
    let mark = null;
    m.on("movestart", () => lift(true));
    m.on("moveend", () => { lift(false); const c = m.getCenter(); onMoved(c.lat, c.lng); });
    setTimeout(() => m.invalidateSize(), 350);
    return {setCenter: (la, lo) => m.flyTo([la, lo], 17, {duration: 0.6}), resize: () => m.invalidateSize(),
      setBranch: (b) => {
        if (mark) mark.remove();
        mark = b && b.lat != null ? L.marker([b.lat, b.lon], {title: b.name, icon: L.divIcon({className: "branch-pin", html: icon("map-pin"), iconSize: [30, 30], iconAnchor: [15, 28]})}).addTo(m) : null;
      }};
  }
  async function makeMap(id, center, onMoved) {
    const box = $(`#${id}`).parentElement, pin = $(".pin", box), dot = $(".pin-dot", box);
    const lift = (up) => { pin.classList.toggle("up", up); dot.classList.toggle("up", up); };
    try { return await yandexMap(id, center, lift, onMoved); } catch (e) { return leafletMap(id, center, lift, onMoved); }
  }
  async function initMap() {
    showBranch();
    if (map) { setTimeout(() => map.resize(), 60); if (point) refreshQuote(); return; }
    const saved = store.get(POINT_KEY, null);
    const center = saved ? [saved.lat, saved.lon] : S.center;
    try {
      map = await makeMap("map", center, moved);
      map.setBranch(branch);
      moved(center[0], center[1]);
      if (!saved) locate(true, map);
    } catch (e) { setQuoteBox("bad", "No se pudo cargar el mapa. Revisa tu conexión."); }
  }
  function moved(la, lo) { addrDirty = false; onPointChange(la, lo); }
  function locate(silent, m) {
    if (!navigator.geolocation) return silent || toast("Tu navegador no permite ubicarte");
    navigator.geolocation.getCurrentPosition((p) => m && m.setCenter(p.coords.latitude, p.coords.longitude),
      (e) => silent || toast(e.code === 1 ? "Activa la ubicación para este sitio en tu navegador" : "No pudimos ubicarte, escribe tu calle o mueve el mapa"),
      {enableHighAccuracy: true, timeout: 10000, maximumAge: 60000});
  }
  // Buscador de dirección: lo que se escribe se busca en Yango y al elegir, el mapa va ahí (el pin sigue mandando).
  function bindSearch(input, list, onPick) {
    let seq = 0;
    const show = (on) => { list.hidden = !on; input.setAttribute("aria-expanded", String(on)); };
    const search = debounce(async () => {
      const q = input.value.trim(), my = ++seq;
      if (q.length < 3) return show(false);
      try {
        const {results} = await api("/address-search", {q});
        if (my !== seq) return;
        list.innerHTML = results.map((r, i) => `<button type="button" role="option" data-s="${i}">${icon("map-pin")}<span><b>${esc(r.title)}</b><small>${esc(r.subtitle)}</small></span></button>`).join("")
          || `<p class="note">Sin resultados. Mueve el mapa hasta tu puerta.</p>`;
        $$("[data-s]", list).forEach((b) => (b.onmousedown = (e) => {
          e.preventDefault(); const r = results[+b.dataset.s];
          show(false); input.value = r.title; onPick(r);
        }));
        show(true);
      } catch (e) { show(false); }
    }, 350);
    input.addEventListener("input", search);
    input.addEventListener("blur", () => setTimeout(() => show(false), 150));
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") show(false); });
  }
  bindSearch($("#fAddr"), $("#addrSugg"), (r) => map && map.setCenter(r.lat, r.lon));
  $("#locateBtn").onclick = () => locate(false, map);
  $("#fAddr").addEventListener("input", () => (addrDirty = true));
  $$('input[name="pay"]').forEach((r) => r.addEventListener("change", () => { $("#changeField").hidden = $('input[name="pay"]:checked').value !== "cash"; }));

  // ------------------------------------------------------------------ ubicación primero: qué sucursal te atiende
  let locMap = null, locPoint = null, locBranch = null, locSeq = 0;
  const locStatus = (cls, html) => { $("#locStatus").className = `quote ${cls}`; $("#locText").innerHTML = html; };
  async function openLoc(first) {
    $("#locSkip").hidden = !first;
    open($("#locSheet"));
    const saved = store.get(POINT_KEY, null), c = saved ? [saved.lat, saved.lon] : S.center;
    if (locMap) return setTimeout(() => locMap.resize(), 60);
    try { locMap = await makeMap("locMap", c, locMoved); } catch (e) { return locStatus("bad", "No se pudo cargar el mapa. Revisa tu conexión."); }
    locMoved(c[0], c[1]);
    if (!saved) locate(true, locMap);
  }
  const locMoved = debounce(async (lat, lon) => {
    const seq = ++locSeq;
    locPoint = {lat, lon}; locBranch = null; $("#locOk").disabled = true;
    locStatus("loading", "Buscando la sucursal que te atiende…");
    api("/geocode", {lat, lon}).then((g) => {
      if (seq !== locSeq || !g.ok || !g.address) return;
      locPoint.label = g.address.short_text || g.address.text || ""; locPoint.geocoded = g.address;
      if (document.activeElement !== $("#locQ")) $("#locQ").value = locPoint.label;
    }).catch(() => {});
    try {
      const {branch: b} = await api(`/branch?lat=${lat}&lon=${lon}`);
      if (seq !== locSeq) return;
      locBranch = b; if (locMap) locMap.setBranch(b);
      if (!b) return locStatus("bad", "Por ahora no llegamos a esta dirección. Prueba otra.");
      locStatus(b.available ? "ok" : "bad", b.available ? `Te atiende <b>${esc(b.name)}</b>${b.eta_min ? ` · ${b.eta_min}–${b.eta_max} min` : ""}`
        : `<b>${esc(b.name)}</b> no está llegando a esta zona ahora. Puedes ver su menú y pedir más tarde.`);
      $("#locOk").disabled = false;
    } catch (e) { if (seq === locSeq) locStatus("bad", esc(e.message)); }
  }, 400);
  bindSearch($("#locQ"), $("#locSugg"), (r) => locMap && locMap.setCenter(r.lat, r.lon));
  $("#locLocate").onclick = () => locate(false, locMap);
  $("#locSkip").onclick = () => closeAll();
  $("#locOk").onclick = async () => {
    if (!locBranch) return;
    const p = {...locPoint};
    point = {lat: p.lat, lon: p.lon, geocoded: p.geocoded};
    $("#addrPillText").textContent = p.label || "Mi dirección";
    $("#fAddr").value = p.label || ""; addrDirty = false;
    closeAll();
    if (!branch || branch.slug !== locBranch.slug) { try { await loadMenu(locBranch.slug); } catch (e) { toast(e.message); } }
    savePoint(p);
    if (map) map.setCenter(p.lat, p.lon);
    refreshEta(p);
  };
  function showBranch() {
    if (!branch) return;
    $("#branchLine").hidden = false; $("#branchName").textContent = branch.name;
    $("#metaBranch").hidden = false; $("#metaBranchName").textContent = branch.name.replace(/^.*?\s[-–]\s/, "");
    if (map) map.setBranch(branch);
  }
  function refreshEta(p) {
    api("/quote", {lat: p.lat, lon: p.lon, subtotal: 0, branch: branch && branch.slug}).then((q) => {
      if (q.available && q.eta_min) $("#metaEta").innerHTML = `${icon("bike")}<b>${q.eta_min}–${q.eta_max} min</b>`;
    }).catch(() => {});
  }
  // Menú de la sucursal; si cambia, el carrito pasa a la nueva sucursal buscando cada producto por su nombre.
  async function loadMenu(slug) {
    const prev = menu;
    const m = await api(`/menu${slug ? `?branch=${encodeURIComponent(slug)}` : ""}`);
    menu = m; branch = m.branch;
    if (prev && prev.branch && prev.branch.slug !== branch.slug) moveCart(prev, m);
    renderMenu(); saveCart(); renderBest(); renderTikTok(); showBranch();
  }
  function moveCart(prev, next) {
    const byName = {}; Object.values(next.items).forEach((it) => { byName[norm(it.name)] = byName[norm(it.name)] || it; });
    let lost = 0;
    cart = cart.map((l) => {
      const old = prev.items[l.item_id], it = old && byName[norm(old.name)];
      if (!it) { lost++; return null; }
      const options = {};
      for (const g of old.groups) {
        const ids = l.options[g.id]; if (!ids || !ids.length) continue;
        const ng = it.groups.find((x) => norm(x.name) === norm(g.name));
        const nids = (ids || []).map((oid) => { const o = g.options.find((x) => x.id === oid); const no = o && ng && ng.options.find((x) => norm(x.name) === norm(o.name)); return no && no.id; });
        if (!ng || nids.some((x) => !x)) { lost++; return null; }
        options[ng.id] = nids;
      }
      return {...l, item_id: it.id, options, key: it.id + "|" + JSON.stringify(Object.keys(options).sort().map((k) => [k, [...options[k]].sort()]))};
    }).filter(Boolean);
    toast(lost ? `Te atiende ${next.branch.name}: ${lost} producto(s) no están en esta sucursal` : `Te atiende ${next.branch.name}`);
  }

  // ------------------------------------------------------------------ pedido
  const prof = store.get(PROFILE_KEY, {});
  $("#fFirst").value = prof.first_name || ""; $("#fLast").value = prof.last_name || ""; $("#fPhone").value = prof.phone || "";
  const savedPoint = store.get(POINT_KEY, null);
  if (savedPoint && savedPoint.label) $("#addrPillText").textContent = savedPoint.label;
  async function placeOrder() {
    const err = $("#orderErr");
    const phone = $("#fPhone").value.replace(/\D/g, "");
    const checks = [[!point, "Marca tu ubicación en el mapa", null], [$("#fAddr").value.trim().length < 3, "Escribe la dirección", "#fAddr"],
      [$("#fRef").value.trim().length < 3, "Agrega número de casa o una referencia", "#fRef"], [$("#fFirst").value.trim().length < 2, "Escribe tu nombre", "#fFirst"],
      [$("#fLast").value.trim().length < 2, "Escribe tu apellido", "#fLast"], [!/^[67]\d{7}$/.test(phone.replace(/^591/, "")), "Escribe un celular válido (8 dígitos)", "#fPhone"]];
    const bad = checks.find((c) => c[0]);
    if (bad) { showErr(err, bad[1]); if (bad[2]) { $(bad[2]).focus({preventScroll: true}); $(bad[2]).scrollIntoView({behavior: "smooth", block: "center"}); } return; }
    showErr(err, "");
    const method = $('input[name="pay"]:checked').value;
    const body = {
      customer: {first_name: $("#fFirst").value.trim(), last_name: $("#fLast").value.trim(), phone},
      address: {lat: point.lat, lon: point.lon, text: $("#fAddr").value.trim(), reference: $("#fRef").value.trim(), comment: $("#fNote").value.trim(), geocoded: point.geocoded || null},
      payment: {method, change_for: method === "cash" && $("#fChange").value.trim() ? $("#fChange").value.replace(",", ".") : null},
      items: cart.map((l) => ({item_id: l.item_id, quantity: l.quantity, options: l.options})),
      expected_total: quote.total, branch: branch && branch.slug,
    };
    const btn = $("#placeBtn"); btn.disabled = true; btn.textContent = "Enviando…";
    try {
      const res = await api("/orders", body);
      store.set(PROFILE_KEY, body.customer); cart = []; store.set(CART_KEY, []);
      location.href = res.url;
    } catch (e) { showErr(err, e.message); btn.disabled = false; refreshQuote(); }
  }

  // ------------------------------------------------------------------ inicio
  window.addEventListener("message", (e) => {  // vista previa en vivo desde el portal
    if (e.origin !== location.origin || !e.data || e.data.type !== "theme") return;
    for (const [k, v] of Object.entries(e.data.vars || {})) document.body.style.setProperty(k, v);
  });
  // Ubicación primero: con la dirección guardada se entra directo a su sucursal; sin ella se pregunta.
  (async () => {
    const saved = savedPoint && savedPoint.branch ? savedPoint : null;
    try { await loadMenu(saved && saved.branch); }
    catch (e) {
      try { if (!saved) throw e; await loadMenu(null); }
      catch (e2) { $("#menu").innerHTML = `<p class="note" style="padding:40px 0">${esc(e2.message)}</p>`; return; }
    }
    if (!saved) return new URLSearchParams(location.search).has("preview") ? undefined : openLoc(true);  // vista previa del portal: sin preguntar
    point = {lat: saved.lat, lon: saved.lon};
    refreshEta(saved);
    // La sucursal asignada puede cambiar (horarios, zonas): se revisa sin molestar.
    api(`/branch?lat=${saved.lat}&lon=${saved.lon}`).then(async ({branch: b}) => {
      if (b && branch && b.slug !== branch.slug) { await loadMenu(b.slug); savePoint(saved); }
    }).catch(() => {});
  })();
})();
