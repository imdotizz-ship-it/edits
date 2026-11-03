"use strict";
// Battlelog Profile Control Panel - admin UI. All external text is inserted with
// textContent (via h()), never innerHTML, because dogtag names etc. come from a third party.

const S = { status: null, profiles: [], profileId: null, current: null, scenarioId: null, role: null };
const $ = (sel) => document.querySelector(sel);

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const svg = (tag, attrs = {}) => {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { ...(opts.headers || {}) }, credentials: "same-origin" };
  if (init.method !== "GET") init.headers["X-Panel-Request"] = "1";
  if (opts.json !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.json); }
  if (opts.body !== undefined) init.body = opts.body;
  const res = await fetch(path, init);
  if (res.status === 401) { showLogin(); throw new Error("Sessão expirada"); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) { const e = new Error((data && (data.error || data.reason || (data.error && data.error.message))) || res.statusText); e.data = data; e.status = res.status; throw e; }
  return data;
}

// ---------- formatting ----------
const nf = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
function fmtValue(v, unit) {
  if (v == null) return "—";
  if (unit === "seconds") { const hr = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60); return `${nf.format(hr)}h ${m}m`; }
  if (unit === "percent") return nf.format(v) + "%";
  if (unit === "meters") return nf.format(v) + " m";
  return nf.format(v);
}
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR") : "—");

function badge(kind, text) {
  const map = {
    source: ["real", "● REAL"], "manual-import": ["real", "● REAL · importado"], derived: ["derived", "∑ DERIVADO"],
    simulated: ["sim", "◆ SIMULATED"], "simulated-derived": ["sim", "◆ SIMULATED (calc.)"], missing: ["missing", "○ SEM DADOS"],
    UNLOCKED: ["real", "✔ Desbloqueada"], LOCKED: ["unknown", "✖ Bloqueada"], UNKNOWN: ["unknown", "? Desconhecido"],
    UNSUPPORTED: ["unsupported", "⊘ UNSUPPORTED"], SIMULATED: ["sim", "◆ SIMULATED"], AUTHORIZED_WRITE: ["real", "✔ AUTHORIZED WRITE"],
    AVAILABLE: ["derived", "◇ AVAILABLE"], READ: ["real", "● READ"], SYNC: ["derived", "⟳ SYNC"], ADMIN: ["unknown", "⚙ ADMIN"],
    ok: ["real", "✔ ok"], error: ["unsupported", "✖ erro"], refused: ["unsupported", "⊘ recusado"], running: ["derived", "… em curso"],
  };
  const [cls, label] = map[kind] || ["unknown", kind];
  return h("span", { class: `badge ${cls}` }, text || label);
}
function msg(el, text, kind = "") { el.className = `msg ${kind}`; el.textContent = text; }
function table(headers, rows) {
  return h("table", {}, h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => (c instanceof Node && c.tagName === "TD" ? c : h("td", {}, c)))))));
}

// ---------- session ----------
function showLogin() { $("#login").hidden = false; $("#loginToken").focus(); }
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/login", { method: "POST", json: { token: $("#loginToken").value } }); $("#loginToken").value = ""; $("#login").hidden = true; boot(); }
  catch (err) { msg($("#loginMsg"), err.message, "error"); }
});
$("#logoutBtn").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); showLogin(); });

// ---------- tabs ----------
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]"); if (!b) return;
  document.querySelectorAll("#tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${b.dataset.tab}`));
  render(b.dataset.tab);
});
const activeTab = () => document.querySelector("#tabs button[aria-selected=true]").dataset.tab;

// ---------- boot ----------
async function boot() {
  const me = await api("/api/me");
  if (!me.role) return showLogin();
  S.role = me.role;
  S.status = await api("/api/status");
  const flags = $("#flags"); flags.replaceChildren(
    h("span", { class: `badge env-${S.status.environment}` }, `AMBIENTE: ${S.status.environment.toUpperCase()}`),
    badge("UNSUPPORTED", S.status.writes_enabled ? "GRAVAÇÃO: flag ligada · registro vazio" : "⊘ GRAVAÇÃO DESATIVADA"),
    h("span", { class: "badge unknown" }, `PAPEL: ${me.role}${me.insecure_dev ? " (dev)" : ""}`));
  const ps = $("#directPlatform"); ps.replaceChildren(...S.status.platforms.map((p) => h("option", { value: p.key }, p.label)));
  await loadProfiles();
  render(activeTab());
}

async function loadProfiles() {
  S.profiles = await api("/api/profiles");
  const sel = $("#profileSelect");
  sel.replaceChildren(...(S.profiles.length ? S.profiles.map((p) => h("option", { value: p.id }, `${p.persona_name} · ${p.platform}`)) : [h("option", { value: "" }, "Nenhum perfil")]));
  if (!S.profileId || !S.profiles.find((p) => p.id === S.profileId)) S.profileId = S.profiles.length ? S.profiles[S.profiles.length - 1].id : null;
  if (S.profileId) sel.value = S.profileId;
  S.current = S.profileId ? await api(`/api/profiles/${S.profileId}/current`).catch(() => null) : null;
}
$("#profileSelect").addEventListener("change", async (e) => { S.profileId = Number(e.target.value) || null; S.scenarioId = null; await loadProfiles(); render(activeTab()); });

async function render(tab) {
  try {
    ({ connection: renderConnection, stats: renderStats, dogtags: renderDogtags, lab: renderLab, sync: renderSync,
       audit: renderAudit, feasibility: renderFeasibility })[tab]();
  } catch (err) { console.error(err); }
}

// ---------- connection ----------
async function renderConnection() {
  S.status = await api("/api/status");
  const st = S.status.sync, c = S.current, box = $("#connectionCard");
  const state = { ok: "Conectado (última consulta OK)", error: "Erro na última tentativa", "offline-import-only": "Somente dados importados", "never-connected": "Nunca conectado" }[st.connection];
  const kv = [["Estado", h("span", {}, badge(st.connection === "ok" ? "ok" : st.connection === "error" ? "error" : "UNKNOWN", state))]];
  if (c) {
    const p = c.profile;
    kv.push(["Soldado", p.persona_name], ["Persona ID", h("code", {}, p.persona_id)], ["Plataforma", p.platform],
      ["User ID", p.user_id ? h("code", {}, p.user_id) : "—"], ["Jogo", p.game],
      ["Última sincronização", fmtTime(c.snapshot.captured_at)],
      ["Origem dos dados", h("span", {}, badge(c.snapshot.source_kind === "battlelog-live" ? "source" : "manual-import"), " ", c.snapshot.source_kind)],
      ["URLs consultadas", h("div", {}, c.sources.map((s) => h("div", {}, h("code", {}, s.url))))],
      ["SHA-256 (bruto)", h("code", {}, c.snapshot.raw_sha256.slice(0, 16) + "…")]);
  } else kv.push(["Perfil", "nenhum perfil sincronizado ainda"]);
  if (st.last_attempt && st.last_attempt.status === "error") kv.push(["Último erro", h("span", { class: "warn" }, `${st.last_attempt.error_kind}: ${st.last_attempt.error_message}`)]);
  box.replaceChildren(...kv.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

$("#resolveForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#soldiers"); out.replaceChildren(); msg($("#connectMsg"), "Consultando Battlelog…");
  try {
    const r = await api("/api/resolve", { method: "POST", json: { username: $("#resolveUser").value.trim() } });
    msg($("#connectMsg"), `${r.soldiers.length} soldado(s) encontrado(s).`, "ok");
    out.replaceChildren(table(["Soldado", "Persona ID", "Plataforma", ""], r.soldiers.map((s) => [s.persona_name, h("code", {}, s.persona_id), s.platform || `? (${s.platform_raw})`,
      h("button", { disabled: !s.platform, onclick: () => doSync(s.persona_id, s.platform, s.persona_name, r.user.user_id) }, "Sincronizar")])));
  } catch (err) { msg($("#connectMsg"), connectorMsg(err), "error"); }
});
$("#directForm").addEventListener("submit", (e) => { e.preventDefault(); doSync($("#directPid").value.trim(), $("#directPlatform").value, $("#directName").value.trim() || null); });

function connectorMsg(err) {
  const ce = err.data && (err.data.connector_error || err.data.error);
  if (ce && ce.kind === "network_blocked") return `Rede bloqueada: o servidor do painel não alcança ${ce.url || "o Battlelog"}. Libere o domínio battlelog.battlefield.com ou importe uma captura HAR.`;
  if (ce && ce.message) return `${ce.kind}: ${ce.message}`;
  return err.message;
}
async function doSync(persona_id, platform, persona_name, user_id) {
  msg($("#connectMsg"), "Sincronizando (somente leitura)…");
  try {
    const r = await api("/api/sync", { method: "POST", json: { persona_id, platform, persona_name, user_id } });
    S.profileId = r.profile_id; await loadProfiles();
    const partial = (r.changes.partial_errors || []).map((x) => `${x.resource}: ${x.kind}`).join("; ");
    msg($("#connectMsg"), `Snapshot #${r.snapshot_id} salvo.${partial ? " Parcial — " + partial : ""}`, partial ? "error" : "ok");
    render(activeTab());
  } catch (err) { msg($("#connectMsg"), connectorMsg(err), "error"); renderConnection(); }
}
$("#importForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = $("#importFile").files[0]; if (!f) return;
  msg($("#connectMsg"), "Importando…");
  try {
    const r = await api("/api/import", { method: "POST", body: await f.arrayBuffer(), headers: { "X-Filename": f.name, "Content-Type": "application/octet-stream" } });
    S.profileId = r.profile_id; await loadProfiles(); msg($("#connectMsg"), `Importado como snapshot #${r.snapshot_id} (origem: manual-import).`, "ok"); renderConnection();
  } catch (err) { msg($("#connectMsg"), (err.data && err.data.error && err.data.error.message) || err.message, "error"); }
});

// ---------- stats ----------
const TILE_KEYS = ["kills", "deaths", "kd_ratio", "score", "skill", "wl_ratio", "headshots", "accuracy", "time_played", "rounds", "score_per_minute", "kills_per_minute", "headshot_rate", "wins", "losses"];

function statTile(key, entry, label, extraClass = "") {
  const origin = entry.origin === "source" && entry.source_kind === "manual-import" ? "manual-import" : entry.origin;
  const src = entry.source ? entry.source : entry.derived_from ? `calc.: ${entry.derived_from.join(" ÷ ")}` : "não presente na fonte";
  return h("div", { class: `tile ${extraClass}` }, h("div", { class: "label" }, label), h("div", { class: `value ${entry.value == null ? "none" : ""}` }, fmtValue(entry.value, entry.unit)),
    h("div", {}, badge(origin)), h("div", { class: "src", title: src }, src), entry.fetched_at ? h("div", { class: "src" }, fmtTime(entry.fetched_at)) : null);
}

async function renderStats() {
  const c = S.current, tiles = $("#statTiles"), charts = $("#charts");
  if (!c) { tiles.replaceChildren(h("div", { class: "empty" }, "Sem dados reais ainda. Conecte um perfil ou importe uma captura.")); charts.replaceChildren(); $("#extraStats").replaceChildren(); $("#statsMeta").textContent = ""; return; }
  $("#statsMeta").replaceChildren(badge(c.snapshot.source_kind === "battlelog-live" ? "source" : "manual-import"), ` snapshot #${c.snapshot.id} · ${fmtTime(c.snapshot.captured_at)}`);
  const labels = Object.fromEntries(S.status.stat_catalogue.map((d) => [d.key, d.label]));
  const keys = [...TILE_KEYS, ...S.status.stat_catalogue.filter((d) => d.group === "extra").map((d) => d.key)];
  tiles.replaceChildren(...keys.map((k) => statTile(k, c.stats[k], labels[k])));
  const extra = Object.entries(c.extra_stats || {});
  $("#extraStats").replaceChildren(extra.length ? table(["Campo", "Valor", "Fonte"], extra.map(([k, v]) => [h("code", {}, k), h("td", { class: "num" }, nf.format(v.value)), h("td", { class: "mono" }, v.source)])) : h("p", { class: "muted" }, "Nenhum."));
  const hist = await api(`/api/profiles/${S.profileId}/history`);
  const series = [["kd_ratio", "K/D", "ratio"], ["skill", "Skill", "score"], ["kills", "Kills", "count"], ["score", "Score", "score"]];
  if ($("#historyTable").checked) {
    charts.replaceChildren(h("div", { class: "table-wrap card" }, table(["Capturado", "Origem", ...series.map((s) => s[1])], hist.map((r) => [fmtTime(r.captured_at), r.source_kind, ...series.map(([k, , u]) => h("td", { class: "num" }, fmtValue(r[k], u)))]))));
  } else charts.replaceChildren(...series.map(([k, label, unit]) => lineChart(label, hist.map((r) => ({ t: r.captured_at, v: r[k], src: r.source_kind })), unit)));
}
$("#historyTable").addEventListener("change", renderStats);

function lineChart(title, points, unit) {
  const pts = points.filter((p) => p.v != null);
  const wrap = h("div", { class: "chart" }, h("h4", {}, title));
  if (pts.length < 2) { wrap.append(h("div", { class: "empty" }, pts.length ? "1 snapshot — o gráfico aparece a partir de 2 sincronizações." : "Sem dados.")); return wrap; }
  const W = 320, H = 160, L = 44, R = 8, T = 8, B = 22;
  const xs = pts.map((p) => +new Date(p.t)), ys = pts.map((p) => p.v);
  let y0 = Math.min(...ys), y1 = Math.max(...ys); if (y0 === y1) { y0 -= 1; y1 += 1; } const pad = (y1 - y0) * 0.1; y0 -= pad; y1 += pad;
  const x0 = Math.min(...xs), x1 = Math.max(...xs) === x0 ? x0 + 1 : Math.max(...xs);
  const X = (x) => L + ((x - x0) / (x1 - x0)) * (W - L - R), Y = (y) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title}: ${pts.length} snapshots` });
  const ax = svg("g", { class: "axis" });
  for (let i = 0; i <= 3; i++) { const v = y0 + ((y1 - y0) * i) / 3; ax.append(svg("line", { class: "gridline", x1: L, x2: W - R, y1: Y(v), y2: Y(v) })); const t = svg("text", { x: L - 6, y: Y(v) + 3, "text-anchor": "end" }); t.textContent = fmtValue(Math.round(v * 100) / 100, unit === "seconds" ? "count" : unit); ax.append(t); }
  [x0, x1].forEach((x, i) => { const t = svg("text", { x: X(x), y: H - 6, "text-anchor": i ? "end" : "start" }); t.textContent = new Date(x).toLocaleDateString("pt-BR"); ax.append(t); });
  s.append(ax, svg("path", { class: "line", d: pts.map((p, i) => `${i ? "L" : "M"}${X(xs[i])},${Y(p.v)}`).join("") }));
  const last = pts.length - 1; s.append(svg("circle", { class: "dot", cx: X(xs[last]), cy: Y(ys[last]), r: 4 }));
  const cross = svg("line", { class: "cross", y1: T, y2: H - B, visibility: "hidden" }), hover = svg("circle", { class: "dot", r: 4, visibility: "hidden" });
  const hit = svg("rect", { x: L, y: 0, width: W - L - R, height: H, fill: "transparent" });
  s.append(cross, hover, hit);
  const tip = $("#tooltip");
  hit.addEventListener("pointermove", (e) => {
    const box = s.getBoundingClientRect(), vx = ((e.clientX - box.left) / box.width) * W;
    let i = 0; xs.forEach((x, j) => { if (Math.abs(X(x) - vx) < Math.abs(X(xs[i]) - vx)) i = j; });
    cross.setAttribute("x1", X(xs[i])); cross.setAttribute("x2", X(xs[i])); cross.setAttribute("visibility", "visible");
    hover.setAttribute("cx", X(xs[i])); hover.setAttribute("cy", Y(ys[i])); hover.setAttribute("visibility", "visible");
    tip.hidden = false; tip.replaceChildren(h("strong", {}, fmtValue(ys[i], unit)), h("div", {}, fmtTime(pts[i].t)), h("div", { class: "muted" }, pts[i].src));
    tip.style.left = `${Math.min(e.clientX + 12, innerWidth - 180)}px`; tip.style.top = `${e.clientY + 12}px`;
  });
  hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); hover.setAttribute("visibility", "hidden"); });
  wrap.append(s);
  return wrap;
}

// ---------- dogtags ----------
let dtTimer;
["#dtQuery", "#dtStatus", "#dtCategory"].forEach((id) => $(id).addEventListener("input", () => { clearTimeout(dtTimer); dtTimer = setTimeout(renderDogtags, 200); }));
async function renderDogtags() {
  const out = $("#dogtagTable");
  if (!S.profileId) { out.replaceChildren(h("div", { class: "empty" }, "Nenhum perfil.")); return; }
  const q = new URLSearchParams({ q: $("#dtQuery").value, status: $("#dtStatus").value, category: $("#dtCategory").value });
  const cat = await api(`/api/profiles/${S.profileId}/dogtags?${q}`);
  $("#dogtagMeta").replaceChildren(badge(cat.snapshot.source_kind === "battlelog-live" ? "source" : "manual-import"), ` snapshot #${cat.snapshot.id} · ${fmtTime(cat.snapshot.captured_at)}`);
  if (!cat.available) { $("#dogtagCounts").replaceChildren(); out.replaceChildren(h("div", { class: "empty" }, cat.reason, h("br"), "O endpoint de dogtags é CANDIDATO NÃO VERIFICADO — veja a aba Viabilidade.")); return; }
  const cs = $("#dtCategory"), keep = cs.value;
  cs.replaceChildren(h("option", { value: "" }, "Todas as categorias"), ...cat.categories.map((c) => h("option", { value: c }, c))); cs.value = keep;
  $("#dogtagCounts").replaceChildren(badge("UNLOCKED", `✔ ${cat.counts.UNLOCKED} desbloqueadas`), badge("LOCKED", `✖ ${cat.counts.LOCKED} bloqueadas`), badge("UNKNOWN", `? ${cat.counts.UNKNOWN} sem estado na fonte`), h("span", { class: "muted" }, `${cat.items.length}/${cat.total} exibidas`));
  out.replaceChildren(cat.items.length ? table(["ID", "Nome", "Descrição", "Categoria", "Estado", "Requisitos / origem", "Fonte"], cat.items.map((t) => [
    h("td", { class: "mono" }, t.id), t.name, t.description || "—",
    h("div", {}, t.category, h("div", { class: "src muted" }, t.category_basis)),
    h("div", {}, badge(t.status), h("div", { class: "muted" }, t.status_basis)),
    [t.requirements ? (typeof t.requirements === "string" ? t.requirements : JSON.stringify(t.requirements)) : null, t.unlock_source].filter(Boolean).join(" · ") || "—",
    h("td", { class: "mono" }, t.source)])) : h("div", { class: "empty" }, "Nenhuma dogtag com esses filtros."));
}

// ---------- lab ----------
$("#newScenario").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!S.current) return alert("Sincronize ou importe um perfil primeiro.");
  const ev = await api("/api/lab/scenarios/new", { method: "POST", json: { name: $("#scenarioName").value || "Cenário", base_snapshot_id: S.current.snapshot.id } });
  S.scenarioId = ev.scenario.id; $("#scenarioName").value = ""; renderLab();
});
async function renderLab() {
  await renderLabList();
  const panel = $("#scenarioPanel");
  if (!S.scenarioId) { panel.replaceChildren(h("p", { class: "muted" }, "Crie ou selecione um cenário.")); return; }
  const ev = await api(`/api/lab/scenarios/${S.scenarioId}`);
  drawScenario(ev);
}
function drawScenario(ev) {
  const panel = $("#scenarioPanel"), sid = ev.scenario.id;
  const editable = S.status.stat_catalogue.filter((d) => d.editable);
  const target = h("select", { id: "modTarget" }, editable.map((d) => h("option", { value: d.key }, d.label)));
  const op = h("select", { id: "modOp" }, ["set", "add", "multiply"].map((o) => h("option", { value: o }, { set: "definir =", add: "somar +", multiply: "multiplicar ×" }[o])));
  const val = h("input", { id: "modValue", type: "number", step: "any", required: true, placeholder: "valor" });
  const strat = h("select", { id: "modStrategy", title: "Como tratar K/D e W/L" }, h("option", { value: "adjust_kills" }, "K/D·W/L: ajustar numerador"), h("option", { value: "override" }, "K/D·W/L: sobrescrever"));
  const addStat = h("form", { class: "row wrap", onsubmit: async (e) => { e.preventDefault(); await labCall(`/api/lab/scenarios/${sid}/modifications`, { kind: "stat", target: target.value, op: op.value, value: Number(val.value), strategy: strat.value }); } }, target, op, val, strat, h("button", {}, "Simular"));
  // only tags that are not unlocked in the real snapshot can be "unlocked" in the lab
  const tags = (ev.original.dogtags || []).filter((t) => t.status !== "UNLOCKED");
  const dt = h("select", {}, tags.map((t) => h("option", { value: t.id }, `${t.name} (${t.status})`)));
  const addTag = tags.length ? h("form", { class: "row wrap", onsubmit: async (e) => { e.preventDefault(); await labCall(`/api/lab/scenarios/${sid}/modifications`, { kind: "dogtag", target: dt.value, op: "unlock" }); } }, dt, h("button", {}, "Simular desbloqueio")) : h("p", { class: "muted" }, ev.original.dogtags ? "Todas as dogtags deste snapshot já estão desbloqueadas." : "Snapshot sem dados de dogtags.");

  const mods = ev.modifications.map((m) => h("div", { class: "mod-row" },
    h("div", {}, badge("SIMULATED"), " ", h("code", {}, `${m.kind}:${m.target}`), ` ${m.op} ${m.value ?? ""}`),
    h("div", { class: "row" }, h("button", { class: "secondary", onclick: () => tryLive(sid, m) }, "Aplicar na conta real…"), h("button", { class: "ghost", onclick: () => labCall(`/api/lab/scenarios/${sid}/modifications/${m.id}/delete`, {}) }, "Remover")),
    h("div", { class: "reason" }, "Conta real: ", badge(m.live_status), " ", m.live_reason)));

  const before = ev.original.stats, after = ev.modified.stats;
  const labels = Object.fromEntries(S.status.stat_catalogue.map((d) => [d.key, d.label]));
  const changed = new Set(ev.diff.map((c) => c.key));
  const cmpRows = Object.keys(before).filter((k) => changed.has(k) || ["kills", "deaths", "kd_ratio", "skill", "score", "wl_ratio"].includes(k)).map((k) => {
    const c = ev.diff.find((x) => x.key === k);
    const d = c && c.delta != null ? h("td", { class: `num ${c.delta > 0 ? "delta-up" : "delta-down"}` }, fmtValue(Math.abs(c.delta), before[k].unit)) : h("td", {}, "—");
    return [labels[k] || k, h("td", { class: "num" }, fmtValue(before[k].value, before[k].unit)), h("div", {}, badge(before[k].origin === "source" ? "source" : before[k].origin)),
      h("td", { class: "num" }, fmtValue(after[k].value, after[k].unit)), h("div", {}, badge(after[k].origin)), d];
  });
  panel.replaceChildren(...[
    h("div", { class: "section-head" }, h("h2", {}, ev.scenario.name), h("div", { class: "row wrap" },
      h("button", { class: "ghost", onclick: () => labCall(`/api/lab/scenarios/${sid}/restore`, {}) }, "Restaurar originais"),
      h("a", { class: "btn ghost", href: `/api/lab/scenarios/${sid}/export?format=json`, download: true }, "Exportar JSON (SIMULATED)"),
      h("a", { class: "btn ghost", href: `/api/lab/scenarios/${sid}/export?format=csv`, download: true }, "Exportar CSV (SIMULATED)"),
      h("button", { class: "danger", onclick: async () => { if (confirm("Excluir cenário?")) { await api(`/api/lab/scenarios/${sid}/delete`, { method: "POST" }); S.scenarioId = null; renderLab(); } } }, "Excluir"))),
    h("p", { class: "muted" }, `Base: snapshot #${ev.base_snapshot.id} (${ev.base_snapshot.source_kind}, ${fmtTime(ev.base_snapshot.captured_at)}). `, ev.watermark),
    h("h3", {}, "Editor de cenário"), addStat, addTag,
    ev.warnings.length ? h("div", { class: "warn" }, ev.warnings.map((w) => h("div", {}, "⚠ " + w))) : null,
    h("h3", {}, "Alterações"), mods.length ? h("div", {}, mods) : h("p", { class: "muted" }, "Nenhuma alteração — o cenário é idêntico ao original."),
    h("h3", {}, "Before / After"), h("div", { class: "table-wrap" }, table(["Estatística", "Original", "", "Simulado", "", "Δ"], cmpRows)),
    ev.dogtag_diff.length ? h("div", {}, h("h3", {}, "Dogtags (simulação)"), table(["Dogtag", "Original", "Simulado"], ev.dogtag_diff.map((d) => [d.name, badge(d.before), h("div", {}, badge(d.after), " ", badge("SIMULATED"))]))) : null,
    h("div", { id: "liveMsg", class: "msg" })].filter(Boolean));
}
async function labCall(path, json) {
  try { const ev = await api(path, { method: "POST", json }); drawScenario(ev); renderLabList(); }
  catch (err) { alert(err.message); }
}
async function renderLabList() { renderLabListItems(await api("/api/lab/scenarios")); }
function renderLabListItems(list) {
  $("#scenarioList").replaceChildren(...(list.length ? list.map((s) => h("li", { class: s.id === S.scenarioId ? "active" : "", onclick: () => { S.scenarioId = s.id; renderLab(); } },
    h("strong", {}, s.name), h("div", { class: "muted" }, `snapshot #${s.base_snapshot_id} · ${s.modifications.length} alteração(ões)`))) : [h("li", { class: "muted" }, "Nenhum cenário.")]));
}
async function tryLive(sid, m) {
  if (!confirm(`Solicitar gravação REAL de ${m.kind}:${m.target}?\n\nEstado conhecido: ${m.live_status}\n${m.live_reason}`)) return;
  const confirmation = prompt("Digite a frase de confirmação exigida pela interface oficial (se existir):", "") || "";
  try { await api(`/api/lab/scenarios/${sid}/apply-live`, { method: "POST", json: { modification_id: m.id, confirmation } }); }
  catch (err) { const d = err.data || {}; msg($("#liveMsg"), `${d.status || "ERRO"}: ${d.reason || err.message} (registrado na auditoria)`, "error"); }
}

// ---------- sync ----------
$("#syncNow").addEventListener("click", async () => {
  const c = S.current; if (!c) return alert("Nenhum perfil para atualizar.");
  if (c.snapshot.source_kind !== "battlelog-live" && !confirm("Este perfil veio de importação. Tentar consulta online?")) return;
  await doSync(c.profile.persona_id, c.profile.platform, c.profile.persona_name, c.profile.user_id); renderSync();
});
async function renderSync() {
  S.status = await api("/api/status");
  const st = S.status.sync;
  $("#syncStatus").replaceChildren(...[
    ["Pendente", st.pending ? h("span", { class: "warn" }, "⚠ sim — " + st.pending_reasons.join("; ")) : "✔ não"],
    ["Conexão", st.connection], ["Última tentativa", st.last_attempt ? `${fmtTime(st.last_attempt.finished_at)} (${st.last_attempt.status})` : "—"],
    ["Último sucesso", st.last_success ? fmtTime(st.last_success.finished_at) : "—"],
    ["Sincronização automática", st.auto_sync_minutes ? `a cada ${st.auto_sync_minutes} min` : "desativada (BLPANEL_AUTO_SYNC_MINUTES)"],
    ["Export do site", st.export_path ? h("code", {}, st.export_path) : "—"],
  ].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  const runs = await api("/api/sync/runs");
  $("#runs").replaceChildren(runs.length ? table(["#", "Início", "Gatilho", "Estado", "Snapshot", "Mudanças / erro"], runs.map((r) => [String(r.id), fmtTime(r.started_at), r.trigger, badge(r.status),
    r.snapshot_id ? `#${r.snapshot_id}` : "—",
    r.status === "error" ? h("span", { class: "warn" }, `${r.error_kind}: ${r.error_message}`) : r.changes ? (r.changes.first_snapshot ? "primeiro snapshot" : `${r.changes.stats.length} estatística(s), ${r.changes.dogtags.length} dogtag(s)`) + ((r.changes.partial_errors || []).length ? ` · parcial: ${r.changes.partial_errors.map((x) => x.resource + "=" + x.kind).join(", ")}` : "") : "—"])) : h("div", { class: "empty" }, "Nenhuma sincronização."));
  if (S.profileId) {
    const hist = await api(`/api/profiles/${S.profileId}/history`);
    const opts = () => hist.map((r) => h("option", { value: r.snapshot_id }, `#${r.snapshot_id} · ${fmtTime(r.captured_at)}`));
    $("#diffA").replaceChildren(...opts()); $("#diffB").replaceChildren(...opts());
    if (hist.length > 1) $("#diffA").value = hist[hist.length - 2].snapshot_id;
    if (hist.length) $("#diffB").value = hist[hist.length - 1].snapshot_id;
  }
}
$("#diffBtn").addEventListener("click", async () => {
  const d = await api(`/api/diff?a=${$("#diffA").value}&b=${$("#diffB").value}`);
  $("#diffOut").replaceChildren(d.changes.length ? table(["Estatística", "Antes", "Depois", "Δ"], d.changes.map((c) => [c.label, h("td", { class: "num" }, c.before == null ? "—" : nf.format(c.before)), h("td", { class: "num" }, c.after == null ? "—" : nf.format(c.after)), h("td", { class: `num ${c.delta > 0 ? "delta-up" : c.delta < 0 ? "delta-down" : ""}` }, c.delta == null ? "—" : nf.format(Math.abs(c.delta)))])) : h("p", { class: "muted" }, "Nenhuma diferença."));
});

// ---------- audit ----------
$("#auditFilter").addEventListener("change", renderAudit);
async function renderAudit() {
  const rows = await api(`/api/audit?classification=${encodeURIComponent($("#auditFilter").value)}`);
  $("#auditTable").replaceChildren(rows.length ? table(["Quando", "Ator", "Ação", "Classificação", "Resultado", "Alvo", "Detalhes (redigidos)"], rows.map((r) => [fmtTime(r.ts), r.actor, r.action, badge(r.classification), badge(r.outcome), r.target || "—", h("td", { class: "mono" }, JSON.stringify(r.details).slice(0, 300))])) : h("div", { class: "empty" }, "Sem registros."));
}

// ---------- feasibility ----------
async function renderFeasibility() {
  const f = await api("/api/feasibility");
  const names = { stat: "Alterar estatísticas (kills, deaths, skill, K/D …)", dogtag_unlock: "Desbloquear / conceder dogtag", dogtag_equip: "Equipar dogtag já possuída" };
  $("#feasibility").replaceChildren(h("div", { class: "table-wrap card" }, table(["Operação na conta real", "Estado", "Motivo"], f.matrix.map((m) => [names[m.kind] || m.kind, badge(m.status), m.reason]))),
    h("p", { class: "muted" }, `Capacidades oficiais registradas: ${f.registered_capabilities.length || "nenhuma"} · BLPANEL_WRITES_ENABLED=${f.writes_enabled} · ambiente ${f.environment}`));
  const ev = { PRIOR_KNOWLEDGE_UNVERIFIED: "conhecimento prévio — validar com probe", CANDIDATE_UNVERIFIED: "candidato — não verificado", VERIFIED: "verificado" };
  $("#endpoints").replaceChildren(table(["Recurso", "Caminho (GET)", "Finalidade", "Evidência"], Object.entries(S.status.endpoints).map(([k, e]) => [k, h("td", { class: "mono" }, S.status.base_url + e.path), e.purpose, ev[e.evidence] || e.evidence])));
}

boot().catch((e) => console.error(e));
