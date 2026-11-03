/* Battlelog profile widget for an external site.
 *
 *   <div data-blpanel-widget data-src="https://YOUR-PANEL/site/profile.json"></div>
 *   <script src="https://YOUR-PANEL/site/widget.js" defer></script>
 *
 * Shows only the REAL export (never lab data). Theme via CSS custom properties set
 * on the host element: --blp-bg, --blp-fg, --blp-muted, --blp-accent, --blp-border,
 * --blp-radius, --blp-font.
 */
(function () {
  "use strict";
  var STATS = [["kills", "Kills"], ["deaths", "Deaths"], ["kd_ratio", "K/D"], ["skill", "Skill"], ["score", "Score"],
    ["wl_ratio", "W/L"], ["headshots", "Headshots"], ["accuracy", "Precisão"], ["time_played", "Tempo"], ["rounds", "Partidas"]];
  var nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  function fmt(e) {
    if (!e || e.value == null) return "—";
    if (e.unit === "seconds") return nf.format(Math.floor(e.value / 3600)) + "h";
    if (e.unit === "percent") return nf.format(e.value) + "%";
    return nf.format(e.value);
  }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  var CSS = ":host{display:block;font-family:var(--blp-font,system-ui,sans-serif);color:var(--blp-fg,#0b0b0b)}" +
    ".card{background:var(--blp-bg,#fcfcfb);border:1px solid var(--blp-border,#dcdbd5);border-radius:var(--blp-radius,10px);padding:14px}" +
    ".head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline}.name{font-weight:700;font-size:16px}" +
    ".muted{color:var(--blp-muted,#6d6c67);font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:10px;margin-top:10px}" +
    ".v{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}.l{font-size:11px;color:var(--blp-muted,#6d6c67)}" +
    ".badge{font-size:10px;border:1px solid var(--blp-accent,#0ca30c);color:var(--blp-accent,#0ca30c);border-radius:999px;padding:1px 6px}";
  function render(host) {
    var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
    var style = el("style"); style.textContent = CSS; root.appendChild(style);
    var card = el("div", "card"); card.appendChild(el("div", "muted", "Carregando estatísticas…")); root.appendChild(card);
    fetch(host.getAttribute("data-src"), { credentials: "omit" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status); return r.json();
    }).then(function (d) {
      if (d.data_classification !== "REAL") throw new Error("export is not classified as REAL data");
      card.textContent = "";
      var head = el("div", "head");
      head.appendChild(el("span", "name", d.profile.persona_name));
      head.appendChild(el("span", "badge", "● dados reais · " + d.snapshot.source_kind));
      card.appendChild(head);
      card.appendChild(el("div", "muted", d.profile.platform.toUpperCase() + " · " + d.profile.game.toUpperCase() +
        " · atualizado " + new Date(d.snapshot.captured_at).toLocaleString()));
      var grid = el("div", "grid");
      STATS.forEach(function (s) {
        var e = d.stats[s[0]]; if (!e || e.value == null) return;
        var c = el("div"); c.appendChild(el("div", "v", fmt(e))); c.appendChild(el("div", "l", s[1])); grid.appendChild(c);
      });
      card.appendChild(grid);
      card.appendChild(el("div", "muted", "Fonte: Battlelog (somente leitura)"));
    }).catch(function (e) { card.textContent = ""; card.appendChild(el("div", "muted", "Estatísticas indisponíveis (" + e.message + ")")); });
  }
  function init() { document.querySelectorAll("[data-blpanel-widget]").forEach(function (n) { if (!n.__blp) { n.__blp = 1; render(n); } }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
