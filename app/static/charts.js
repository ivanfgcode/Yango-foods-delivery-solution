/* Lienzos del tablero, traídos del picker de restotec-yango: línea con relleno tenue y el máximo en naranja, barras,
   dial, anillo, barras horizontales, tramos y tira de calor. Leen los colores de las variables de admin.css. */
(() => {
  const nf = new Intl.NumberFormat("es-BO", {maximumFractionDigits: 0});
  const nf1 = new Intl.NumberFormat("es-BO", {maximumFractionDigits: 1});
  const fmt = (v, d = 0) => v == null || isNaN(v) ? "—" : (d === 1 ? nf1 : nf).format(v);
  const fecha = (d) => d ? `${d.slice(8, 10)}/${d.slice(5, 7)}` : "—";

  function tinta() {
    const cs = getComputedStyle(document.documentElement), v = (n) => cs.getPropertyValue(n).trim();
    return {t: v("--in-tinta"), n: v("--in-nar"), tenue: v("--in-tenue"), filo: v("--in-filo"), suave: v("--in-suave")};
  }
  function lienzo(cv) {
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(1, Math.round(r.width * dpr)); cv.height = Math.max(1, Math.round(r.height * dpr));
    const c = cv.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, r.width, r.height);
    return {c, w: r.width, h: r.height};
  }
  function alfa(hex, a) {
    if (!hex.startsWith("#")) return hex;
    const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
  }
  function vacio(c, w, h, k) { c.font = '12px "JetBrains Mono", monospace'; c.fillStyle = k.tenue; c.textAlign = "center"; c.fillText("sin datos", w / 2, h / 2 + 4); }
  function rrect(c, x, y, w, h, r) { c.beginPath(); c.roundRect ? c.roundRect(x, y, w, h, r) : c.rect(x, y, w, h); c.fill(); }

  function ejes(c, w, h, pad, max, k, pts, fv, etqEje) {
    c.font = '11px "JetBrains Mono", monospace'; c.fillStyle = k.tenue; c.strokeStyle = k.filo; c.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const v = max * i / 4, y = h - pad.b - v / max * (h - pad.t - pad.b);
      c.beginPath(); c.moveTo(pad.l, y); c.lineTo(w - pad.r, y); c.stroke();
      c.textAlign = "right"; c.fillText(fv(v, true), pad.l - 8, y + 4);
    }
    const cada = Math.max(1, Math.ceil(pts.length / Math.max(2, Math.floor((w - pad.l) / 64))));
    c.textAlign = "center";
    pts.forEach((p, i) => {
      if (i % cada) return;
      const paso = (w - pad.l - pad.r) / (etqEje ? pts.length : Math.max(1, pts.length - 1));
      c.fillText(etqEje ? etqEje(p.x) : fecha(p.x), pad.l + i * paso + (etqEje ? paso / 2 : 0), h - 6);
    });
  }
  function puntero(cv, n, X, Y, html) {
    const caja = cv.parentElement; let tip = caja.querySelector(".tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "tip oculto"; caja.appendChild(tip); }
    cv.onmousemove = (e) => {
      const r = cv.getBoundingClientRect(), mx = e.clientX - r.left; let best = 0, d = 1e9;
      for (let i = 0; i < n; i++) { const dd = Math.abs(X(i) - mx); if (dd < d) { d = dd; best = i; } }
      tip.innerHTML = html(best); tip.classList.remove("oculto");
      const tw = tip.offsetWidth; tip.style.left = Math.min(r.width - tw - 4, Math.max(4, X(best) - tw / 2)) + "px";
      tip.style.top = Math.max(0, Y(best) - tip.offsetHeight - 12) + "px";
    };
    cv.onmouseleave = () => tip.classList.add("oculto");
  }
  // Línea con relleno tenue; el máximo en naranja.
  function linea(cv, pts, o = {}) {
    const {c, w, h} = lienzo(cv), k = tinta(); if (pts.length < 2) return vacio(c, w, h, k);
    const pad = o.ejes ? {l: 48, r: 12, t: 12, b: 24} : {l: 2, r: 8, t: 6, b: 4};
    const max = Math.max(...pts.map((p) => p.v)) || 1, X = (i) => pad.l + i * (w - pad.l - pad.r) / (pts.length - 1), Y = (v) => h - pad.b - v / max * (h - pad.t - pad.b);
    if (o.ejes) ejes(c, w, h, pad, max, k, pts, o.fv);
    const g = c.createLinearGradient(0, pad.t, 0, h - pad.b); g.addColorStop(0, alfa(k.t, .16)); g.addColorStop(1, alfa(k.t, 0));
    c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(i), Y(p.v)) : c.moveTo(X(i), Y(p.v)));
    c.lineTo(X(pts.length - 1), h - pad.b); c.lineTo(X(0), h - pad.b); c.closePath(); c.fillStyle = g; c.fill();
    c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(i), Y(p.v)) : c.moveTo(X(i), Y(p.v)));
    c.strokeStyle = k.t; c.lineWidth = o.ejes ? 1.8 : 1.5; c.lineJoin = "round"; c.stroke();
    const im = pts.reduce((b, p, i) => p.v > pts[b].v ? i : b, 0);
    c.fillStyle = k.n; c.beginPath(); c.arc(X(im), Y(pts[im].v), o.ejes ? 4.5 : 3.2, 0, 7); c.fill();
    c.strokeStyle = alfa(k.n, .35); c.lineWidth = 6; c.beginPath(); c.arc(X(im), Y(pts[im].v), o.ejes ? 8 : 6, 0, 7); c.stroke();
    if (o.ejes) puntero(cv, pts.length, X, (i) => Y(pts[i].v), (i) => `${o.etq ? o.etq(pts[i].x) : pts[i].x}<br><b>${o.fv(pts[i].v)}</b>`);
  }
  function barras(cv, pts, o = {}) {
    const {c, w, h} = lienzo(cv), k = tinta(); if (!pts.length || !pts.some((p) => p.v)) return vacio(c, w, h, k);
    const pad = o.ejes ? {l: 48, r: 12, t: 12, b: 24} : {l: 0, r: 0, t: 6, b: 2};
    const max = Math.max(...pts.map((p) => p.v)) || 1, bw = (w - pad.l - pad.r) / pts.length, gap = Math.min(bw * .28, 6);
    if (o.ejes) ejes(c, w, h, pad, max, k, pts, o.fv, o.etqEje || ((x) => fecha(String(x))));
    const im = pts.reduce((b, p, i) => p.v > pts[b].v ? i : b, 0);
    pts.forEach((p, i) => {
      const bh = Math.max(p.v ? 2 : 0, p.v / max * (h - pad.t - pad.b)), x = pad.l + i * bw + gap / 2, r = Math.min(3, (bw - gap) / 2);
      c.fillStyle = i === im ? k.n : alfa(k.t, o.ejes ? .7 : .55); rrect(c, x, h - pad.b - bh, bw - gap, bh, [r, r, 0, 0]);
    });
    if (o.ejes) puntero(cv, pts.length, (i) => pad.l + (i + .5) * bw, (i) => h - pad.b - pts[i].v / max * (h - pad.t - pad.b), (i) => `${o.etq ? o.etq(pts[i].x) : pts[i].x}<br><b>${o.fv(pts[i].v)}</b>`);
  }
  // Dial de aguja: valor frente a su referencia (0..1).
  function dial(cv, frac) {
    const {c, w, h} = lienzo(cv), k = tinta(), cx = w / 2, cy = h - 6, r = Math.min(w / 2 - 8, h - 14);
    c.lineCap = "round";
    for (let i = 0; i <= 40; i++) {
      const a = Math.PI + i / 40 * Math.PI, l = i % 5 ? 5 : 10;
      c.strokeStyle = i / 40 <= frac ? alfa(k.t, .85) : alfa(k.t, .2); c.lineWidth = i % 5 ? 1.2 : 1.8;
      c.beginPath(); c.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r); c.lineTo(cx + Math.cos(a) * (r - l), cy + Math.sin(a) * (r - l)); c.stroke();
    }
    const a = Math.PI + Math.max(0, Math.min(1, frac || 0)) * Math.PI;
    c.strokeStyle = k.t; c.lineWidth = 2; c.beginPath(); c.moveTo(cx, cy); c.lineTo(cx + Math.cos(a) * (r - 16), cy + Math.sin(a) * (r - 16)); c.stroke();
    c.fillStyle = k.n; c.beginPath(); c.arc(cx + Math.cos(a) * (r - 16), cy + Math.sin(a) * (r - 16), 3.4, 0, 7); c.fill();
    c.fillStyle = k.t; c.beginPath(); c.arc(cx, cy, 3.5, 0, 7); c.fill();
  }
  // Anillo de partes (0..1); la primera en naranja.
  function anillo(cv, partes, l1, l2) {
    const {c, w, h} = lienzo(cv), k = tinta(), r = Math.min(w, h) / 2 - 6, cx = w - r - 8, cy = h / 2;
    c.lineWidth = 9; c.strokeStyle = alfa(k.t, .1); c.beginPath(); c.arc(cx, cy, r, 0, 7); c.stroke();
    let a = -Math.PI / 2;
    partes.forEach((p, i) => {
      if (!p) return;
      const b = a + p * Math.PI * 2; c.strokeStyle = i === 0 ? k.n : alfa(k.t, .85 - i * .15);
      c.beginPath(); c.arc(cx, cy, r, a + .03, Math.max(a + .03, b - .03)); c.stroke(); a = b;
    });
    c.font = '500 13px "JetBrains Mono", monospace'; c.fillStyle = k.t; c.textAlign = "center"; c.fillText(Math.round((partes[0] || 0) * 100) + "%", cx, cy + 4);
    c.textAlign = "left"; c.font = '11px "JetBrains Mono", monospace'; c.fillStyle = k.tenue;
    if (l1) c.fillText(l1, 2, h - 22); if (l2) c.fillText(l2, 2, h - 8);
  }
  function hbarras(cv, items) {
    const {c, w, h} = lienzo(cv), k = tinta(); if (!items.length) return vacio(c, w, h, k);
    const n = Math.min(items.length, 4), fila = h / n, max = items[0].v || 1;
    c.font = '11px "JetBrains Mono", monospace';
    items.slice(0, n).forEach((it, i) => {
      const y = i * fila + fila / 2;
      c.fillStyle = alfa(k.t, .1); c.fillRect(0, y + 4, w, 3);
      c.fillStyle = i === 0 ? k.n : alfa(k.t, .7); c.fillRect(0, y + 4, Math.max(3, it.v / max * w), 3);
      c.fillStyle = i === 0 ? k.t : k.tenue; c.textAlign = "left";
      c.fillText(it.l.length > 34 ? it.l.slice(0, 33) + "…" : it.l, 0, y - 2);
    });
  }
  // Tramos: cada parte un segmento, la mayor en naranja.
  function tramos(cv, vals, pie) {
    const {c, w, h} = lienzo(cv), k = tinta(); if (!vals.length || !vals.some(Boolean)) return vacio(c, w, h, k);
    const tot = vals.reduce((s, v) => s + v, 0) || 1, y = h / 2 - 9; let x = 0;
    vals.forEach((v, i) => {
      const ww = v / tot * w; c.fillStyle = i === 0 ? k.n : alfa(k.t, Math.max(.18, .8 - i * .12));
      rrect(c, x + 1, y, Math.max(1, ww - 2), 18, 5); x += ww;
    });
    c.font = '11px "JetBrains Mono", monospace'; c.fillStyle = k.tenue; c.textAlign = "left";
    c.fillText(pie || `la mayor: ${Math.round(vals[0] / tot * 100)}%`, 0, h - 4);
  }
  // Tira de calor de 24 horas.
  function calor(cv, vals) {
    const {c, w, h} = lienzo(cv), k = tinta(), max = Math.max(...vals) || 1, cw = w / 24, top = 4, ch = h - 22;
    vals.forEach((v, i) => { c.fillStyle = v === max && v ? k.n : alfa(k.t, .06 + v / max * .74); rrect(c, i * cw + 1, top, cw - 2, ch, 3); });
    c.font = '10px "JetBrains Mono", monospace'; c.fillStyle = k.tenue; c.textAlign = "center";
    [0, 6, 12, 18, 23].forEach((i) => c.fillText(String(i).padStart(2, "0"), i * cw + cw / 2, h - 4));
  }
  window.IN = {fmt, fecha, linea, barras, dial, anillo, hbarras, tramos, calor};
})();
