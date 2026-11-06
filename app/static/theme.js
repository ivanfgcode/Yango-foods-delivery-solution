/* Tema claro / oscuro de los paneles (portal del restaurante, cadena, cabina de operadores; el admin usa la misma
   clave). Se carga en <head> para no parpadear. Cualquier botón con [data-tema-btn] cambia el tema y se recuerda. */
(() => {
  const KEY = "pp_tema", root = document.documentElement;
  const get = () => { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
  const set = (v) => { try { localStorage.setItem(KEY, v); } catch (e) {} };
  // data-tema-defecto="sistema" (cadena, cabina) sigue al sistema hasta que se elige; si no, oscuro (portal).
  const sys = () => (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "claro" : "oscuro");
  const initial = () => get() || (root.dataset.temaDefecto === "sistema" ? sys() : "oscuro");
  function apply(t) {
    const claro = t === "claro";
    if (claro) root.dataset.tema = "claro"; else delete root.dataset.tema;  // portal.css / admin.css
    root.dataset.theme = claro ? "light" : "dark";                           // panel.css
    document.querySelectorAll("[data-tema-btn]").forEach((b) => {
      b.setAttribute("aria-pressed", String(claro));
      b.title = claro ? "Cambiar a versión oscura" : "Cambiar a versión clara";
      const l = b.querySelector("[data-tema-lbl]"); if (l) l.textContent = claro ? "Oscuro" : "Claro";
      const i = b.querySelector("[data-tema-ic]"); if (i) i.textContent = claro ? "🌙" : "☀️";
    });
  }
  apply(initial());
  document.addEventListener("DOMContentLoaded", () => apply(initial()));
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-tema-btn]"); if (!b) return;
    const next = root.dataset.tema === "claro" ? "oscuro" : "claro";
    set(next); apply(next);
    window.dispatchEvent(new CustomEvent("tema", {detail: next}));  // los gráficos se repintan con los colores nuevos
  });
})();
