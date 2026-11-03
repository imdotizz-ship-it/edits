/* Battlelog profile widget for an external site.
 *
 *   <div data-blpanel-widget data-src="https://YOUR-PANEL/site/profile.json"></div>
 *   <script src="https://YOUR-PANEL/site/widget.js" defer></script>
 *
 * Two kinds of file:
 *   /site/profile.json   REAL data  -> "dados reais" badge
 *   /site/showcase.json  custom build from the lab -> always shows the fixed
 *                        disclosure below and marks every changed number. There is
 *                        no attribute or CSS variable that hides the disclosure.
 *
 * Theme via CSS custom properties set on the host element: --blp-bg, --blp-fg, --blp-muted, --blp-accent, --blp-border,
 * --blp-radius, --blp-font.
 */
(function () {
  "use strict";
  var STATS = [["kills", "Kills"], ["deaths", "Deaths"], ["kd_ratio", "K/D"], ["skill", "Skill"], ["score", "Score"],
    ["wl_ratio", "W/L"], ["headshots", "Headshots"], ["accuracy", "Precisão"], ["time_played", "Tempo"], ["rounds", "Partidas"]];
  var DISCLOSURE = "Build personalizado · não é estatística oficial do Battlelog";
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
    ".badge{font-size:10px;border:1px solid var(--blp-accent,#0ca30c);color:var(--blp-accent,#0ca30c);border-radius:999px;padding:1px 6px}" +
    ".disclosure{display:block!important;visibility:visible!important;opacity:1!important;margin:-14px -14px 10px;padding:8px 14px;font-size:12px;font-weight:700;" +
    "color:#0b0b0b;border-bottom:1px solid #fab219;border-radius:var(--blp-radius,10px) var(--blp-radius,10px) 0 0;" +
    "background:repeating-linear-gradient(135deg,#fde3a7 0 8px,#fff4d6 8px 16px)}" +
    ".c{font-size:10px;font-weight:700;color:#8a5a00;margin-left:3px}.real{font-size:10px;color:var(--blp-muted,#6d6c67)}";
  function render(host) {
    var root = host.attachShadow ? host.attachShadow({ mode: "closed" }) : host;
    var style = el("style"); style.textContent = CSS; root.appendChild(style);
    var card = el("div", "card"); card.appendChild(el("div", "muted", "Carregando estatísticas…")); root.appendChild(card);
    fetch(host.getAttribute("data-src"), { credentials: "omit" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status); return r.json();
    }).then(function (d) {
      card.textContent = "";
      if (d.data_classification === "SIMULATED") return renderShowcase(card, d);
      if (d.data_classification !== "REAL") throw new Error("unknown data classification");
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
  function renderShowcase(card, d) {
    card.appendChild(el("div", "disclosure", "◆ " + DISCLOSURE));
    var head = el("div", "head");
    head.appendChild(el("span", "name", (d.persona_name || "") + (d.title ? " — " + d.title : "")));
    card.appendChild(head);
    card.appendChild(el("div", "muted", (d.platform || "").toUpperCase() + " · publicado " + new Date(d.published_at).toLocaleString()));
    var grid = el("div", "grid");
    STATS.forEach(function (s) {
      var e = d.stats[s[0]]; if (!e || e.value == null) return;
      var c = el("div"); var v = el("div", "v", fmt(e));
      if (e.custom) v.appendChild(el("span", "c", "CUSTOM"));
      c.appendChild(v); c.appendChild(el("div", "l", s[1]));
      if (e.custom && e.real_value != null) c.appendChild(el("div", "real", "real: " + fmt({ value: e.real_value, unit: e.unit })));
      grid.appendChild(c);
    });
    card.appendChild(grid);
    (d.custom_dogtags || []).forEach(function (t) { card.appendChild(el("div", "muted", "Dogtag (custom): " + t.name)); });
    card.appendChild(el("div", "muted", DISCLOSURE));
  }
  function init() { document.querySelectorAll("[data-blpanel-widget]").forEach(function (n) { if (!n.__blp) { n.__blp = 1; render(n); } }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
