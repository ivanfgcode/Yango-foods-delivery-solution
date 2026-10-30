/* Menu Deli Bo · landing: aparición al hacer scroll, palabra que rota, avisos sobre el teléfono, inclinación con el
   ratón, antes/después animado, calculadora de ahorro y formulario de contacto. Respeta «reducir movimiento». */
(() => {
  const $ = (s, el = document) => el.querySelector(s), $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const C = window.MDB, nf = new Intl.NumberFormat("es-BO", {maximumFractionDigits: 0});
  const money = (n) => `${C.currency}${nf.format(Math.max(0, Math.round(n)))}`;

  // Aparición al hacer scroll.
  const io = "IntersectionObserver" in window && !reduced ? new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), {rootMargin: "0px 0px -8% 0px", threshold: .08}) : null;
  $$(".rv").forEach((el) => (io ? io.observe(el) : el.classList.add("in")));

  // Palabra que rota en el titular.
  const words = $$("#rot span");
  let wi = 0;
  if (!reduced && words.length > 1) setInterval(() => {
    const cur = words[wi]; wi = (wi + 1) % words.length; const nxt = words[wi];
    cur.classList.remove("on"); cur.classList.add("out");
    nxt.classList.remove("out"); nxt.classList.add("on");
    setTimeout(() => cur.classList.remove("out"), 700);
  }, 2400);

  // Avisos que aparecen sobre el teléfono, en bucle.
  const toasts = $$(".toast");
  const showToasts = () => toasts.forEach((t, i) => setTimeout(() => t.classList.add("on"), 600 + i * 900));
  showToasts();
  if (!reduced) setInterval(() => { toasts.forEach((t) => t.classList.remove("on")); setTimeout(showToasts, 500); }, 9000);

  // El teléfono se inclina con el ratón.
  const stage = $("#stage"), phone = $("#phoneMain");
  if (stage && phone && !reduced && matchMedia("(hover: hover)").matches) {
    stage.addEventListener("pointermove", (e) => {
      const r = stage.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
      phone.style.transform = `rotateY(${-12 + x * 16}deg) rotateX(${6 - y * 12}deg)`;
    });
    stage.addEventListener("pointerleave", () => (phone.style.transform = ""));
  }

  // Barra de arriba: fondo al bajar; se esconde al seguir bajando y vuelve al subir. Botón fijo abajo en el móvil.
  let lastY = 0;
  const onScroll = () => {
    const y = scrollY;
    $("#nav").classList.toggle("solid", y > 30);
    $("#nav").style.transform = y > 500 && y > lastY ? "translateY(-110%)" : "";
    $("#stickyCta").classList.toggle("on", y > 520 && innerHeight + y < document.body.scrollHeight - 260);
    lastY = y;
  };
  addEventListener("scroll", onScroll, {passive: true}); onScroll();
  const drawer = $("#drawer");
  const setMenu = (on) => { drawer.classList.toggle("open", on); drawer.setAttribute("aria-hidden", String(!on)); $("#menuBtn").setAttribute("aria-expanded", String(on)); document.body.style.overflow = on ? "hidden" : ""; };
  $("#menuBtn").onclick = () => setMenu(true); $("#menuClose").onclick = () => setMenu(false);
  $$("a", drawer).forEach((a) => a.addEventListener("click", () => setMenu(false)));

  // Antes / con Menu Deli Bo: los pasos aparecen uno a uno.
  const TOTAL = {bad: ["25 min", "y una persona pendiente del celular"], good: ["2 min", "y nadie tuvo que contestar un chat"]};
  function play(which) {
    $$(".vs-toggle button").forEach((b) => b.classList.toggle("on", b.dataset.vs === which));
    $("#flowBad").hidden = which !== "bad"; $("#flowGood").hidden = which !== "good";
    const steps = $$(".step", which === "bad" ? $("#flowBad") : $("#flowGood"));
    steps.forEach((s) => s.classList.remove("on"));
    steps.forEach((s, i) => setTimeout(() => s.classList.add("on"), reduced ? 0 : 120 + i * 180));
    $("#vsTime").textContent = TOTAL[which][0]; $("#vsText").textContent = TOTAL[which][1];
  }
  $$(".vs-toggle button").forEach((b) => (b.onclick = () => play(b.dataset.vs)));
  const flowObs = "IntersectionObserver" in window ? new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) { play("bad"); flowObs.disconnect(); if (!reduced) setTimeout(() => play("good"), 4200); }
  }), {threshold: .3}) : null;
  flowObs ? flowObs.observe($("#flowBad")) : play("bad");

  // Calculadora de ahorro (estimación con los valores que pone el restaurante).
  const val = (id) => +$(id).value || 0;
  let shown = 0;
  function animateTo(el, to) {
    if (reduced) { el.textContent = money(to); shown = to; return; }
    const from = shown, t0 = performance.now();
    const tick = (t) => { const k = Math.min(1, (t - t0) / 500), e = 1 - Math.pow(1 - k, 3); el.textContent = money(from + (to - from) * e); if (k < 1) requestAnimationFrame(tick); else shown = to; };
    requestAnimationFrame(tick);
  }
  function calc() {
    const orders = val("#cOrders"), ticket = val("#cTicket"), branches = val("#cBranches"), pct = val("#cCommission"), salary = val("#cSalary");
    $("#vOrders").textContent = nf.format(orders); $("#vTicket").textContent = nf.format(ticket);
    $("#vBranches").textContent = branches; $("#vCommission").textContent = `${pct} %`;
    const sales = orders * ticket, commission = sales * pct / 100, plan = C.price * branches;
    $("#oSales").textContent = money(sales); $("#oCommission").textContent = money(commission);
    $("#oPlan").textContent = `- ${money(plan)}`; $("#oSalarySave").textContent = money(salary - plan);
    animateTo($("#oSave"), commission - plan);
  }
  ["#cOrders", "#cTicket", "#cBranches", "#cCommission", "#cSalary"].forEach((id) => $(id).addEventListener("input", calc));
  calc();

  // Formulario de contacto.
  $("#leadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, msg = $("#leadMsg"), btn = $("button", f), body = Object.fromEntries(new FormData(f));
    btn.disabled = true; msg.className = "msg"; msg.textContent = "Enviando…";
    try {
      const r = await fetch("/api/leads", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(body)});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "No pudimos enviar tus datos, intenta de nuevo");
      f.reset(); msg.className = "msg ok"; msg.textContent = "¡Listo! Te escribimos muy pronto.";
    } catch (err) { msg.className = "msg bad"; msg.textContent = err.message; }
    btn.disabled = false;
  });
})();
