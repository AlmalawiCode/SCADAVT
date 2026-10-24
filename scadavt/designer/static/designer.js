/* SCADAVT generic network designer — vanilla, no framework, domain-neutral
 * (no application-specific model baked in).
 *
 * The in-memory document IS the canonical config (scadavt.core.model), so
 * export/import is plain JSON and a Python->GUI->Python round-trip is lossless
 * by construction. Every edit is a pure mutation of `cfg`. Topologies are
 * arbitrary: the user places any nodes and wires any links. The GUI never
 * validates or runs itself — it calls the shared HTTP service (L1/L2).
 */
"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
const NS = 24; // grid
const COLORS = { plc: "var(--plc)", mtu: "var(--mtu)", router: "var(--router)", switch: "var(--switch)" };

// ---- document: the canonical config + a light view layer (positions) --------
let cfg = blankConfig();
let pos = {};            // id -> {x,y}  (presentation only; mirrored into _view on export)
let selected = null;
let linkMode = false, linkFrom = null;
let counter = { plc: 0, mtu: 0, switch: 0, router: 0 };

function blankConfig() {
  return { schema_version: 1, name: "untitled", control: { placement: "mtu" },
           netmask: 24, mtu: null, devices: [], switches: [], routers: [],
           links: [], mapping: [], rules: [] };
}

// node registry over the canonical model ------------------------------------
function allNodes() {
  const out = [];
  cfg.devices.forEach(d => out.push({ id: d.id, kind: d.kind || "plc" }));
  if (cfg.mtu) out.push({ id: cfg.mtu.id, kind: "mtu" });
  cfg.routers.forEach(r => out.push({ id: r.id, kind: "router" }));
  cfg.switches.forEach(s => out.push({ id: s.id, kind: "switch" }));
  return out;
}
function nodeKind(id) { const n = allNodes().find(n => n.id === id); return n && n.kind; }
function nodeObj(id) {
  return cfg.devices.find(d => d.id === id) || (cfg.mtu && cfg.mtu.id === id ? cfg.mtu : null)
    || cfg.routers.find(r => r.id === id) || cfg.switches.find(s => s.id === id);
}

// ---- mutations (the designer's edit contract) ------------------------------
function addNode(kind, x, y) {
  counter[kind] = (counter[kind] || 0) + 1;
  let id;
  if (kind === "plc") { id = "PLC" + counter.plc;
    const i = cfg.devices.length + 1;
    cfg.devices.push({ id, ip: `10.0.1.${cfg.devices.length + 1}`, protocol: "modbus", seed: 2.0,
      points: [{ id: "s", role: "measurement" }, { id: "a", role: "command", is_bool: true }] });
    cfg.mapping.push({ point: "s", device: id, role: "measurement", element: "S" + i });
    cfg.mapping.push({ point: "a", device: id, role: "command", element: "A" + i });
  } else if (kind === "mtu") {
    if (cfg.mtu) { log("only one MTU; existing kept"); return; }
    id = "MTU"; cfg.mtu = { id, ip: "10.0.1.254" };
  } else if (kind === "switch") { id = "SW" + counter.switch; cfg.switches.push({ id }); }
  else if (kind === "router") { id = "R" + counter.router; cfg.routers.push({ id, model: "router" }); }
  pos[id] = { x: snap(x), y: snap(y) };
  render();
  select(id);
}

function removeNode(id) {
  cfg.devices = cfg.devices.filter(d => d.id !== id);
  cfg.routers = cfg.routers.filter(r => r.id !== id);
  cfg.switches = cfg.switches.filter(s => s.id !== id);
  if (cfg.mtu && cfg.mtu.id === id) cfg.mtu = null;
  cfg.links = cfg.links.filter(l => l.a !== id && l.b !== id);
  cfg.mapping = cfg.mapping.filter(m => m.device !== id);
  delete pos[id];
  if (selected === id) selected = null;
  render(); renderProps();
}

function addLink(a, b) {
  if (a === b) return;
  if (cfg.links.some(l => (l.a === a && l.b === b) || (l.a === b && l.b === a))) return;
  // put the endpoint IP on the addressable side (device/mtu/router), bare for switch
  const link = { a, b };
  const ea = nodeObj(a), eb = nodeObj(b);
  if (nodeKind(a) !== "switch" && ea && ea.ip) link.a_ip = ea.ip;
  if (nodeKind(b) !== "switch" && eb && eb.ip) link.b_ip = eb.ip;
  cfg.links.push(link);
  render();
}

function snap(v) { return Math.round(v / NS) * NS; }

// ---- rules text <-> cfg.rules ----------------------------------------------
function rulesToText() {
  return (cfg.rules || []).map(r => {
    const c = (r.if && r.if[0]) || {}, t = (r.then && r.then[0]) || {};
    return `${c.comp} < ${c.value} => ${t.comp} ${t.value || "open"}`;
  }).join("\n");
}
function textToRules(text) {
  const rules = [];
  text.split("\n").map(s => s.trim()).filter(Boolean).forEach((line, i) => {
    const m = line.match(/^(\w+)\s*<\s*([\d.]+)\s*=>\s*(\w+)\s*(\w+)?$/);
    if (!m) { log("skip rule (bad syntax): " + line); return; }
    rules.push({ id: "r" + (i + 1),
      if: [{ kind: "sensor", comp: m[1], attr: "value", op: "below", value: parseFloat(m[2]) }],
      then: [{ comp: m[3], kind: "pump", attr: "status", value: m[4] || "open" }] });
  });
  cfg.rules = rules;
}

// ---- rendering --------------------------------------------------------------
const svg = document.getElementById("canvas");
function render() {
  svg.innerHTML = "";
  // links first
  cfg.links.forEach(l => {
    const pa = pos[l.a], pb = pos[l.b]; if (!pa || !pb) return;
    const ln = el("line", { class: "link", x1: pa.x + 44, y1: pa.y + 18, x2: pb.x + 44, y2: pb.y + 18 });
    svg.appendChild(ln);
  });
  allNodes().forEach(n => {
    const p = pos[n.id] || { x: 40, y: 40 }; pos[n.id] = p;
    const g = el("g", { class: "node" + (selected === n.id ? " sel" : ""), "data-id": n.id,
      transform: `translate(${p.x},${p.y})` });
    g.appendChild(el("rect", { width: 88, height: 36, rx: 8, fill: COLORS[n.kind] || "#888" }));
    const o = nodeObj(n.id);
    g.appendChild(txt(n.id, 44, 16, "text"));
    g.appendChild(txt((o && o.ip) || n.kind, 44, 29, "lab", "middle"));
    svg.appendChild(g);
  });
}
function el(t, attrs) { const e = document.createElementNS(SVGNS, t); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }
function txt(s, x, y, cls, anchor) { const e = el("text", { x, y, class: cls, "text-anchor": anchor || "middle" }); e.textContent = s; return e; }

// ---- selection & properties -------------------------------------------------
function select(id) { selected = id; render(); renderProps(); }
function renderProps() {
  const box = document.getElementById("props");
  if (!selected) { box.innerHTML = '<p class="hint">Select a node to edit.</p>'; return; }
  const o = nodeObj(selected), kind = nodeKind(selected);
  let h = `<div class="pill" style="background:${COLORS[kind]};color:#06101f">${kind}</div>`;
  h += `<label>id</label><input id="p_id" value="${o.id}">`;
  if (kind !== "switch") h += `<label>ip</label><input id="p_ip" value="${o.ip || ""}">`;
  if (kind === "plc") h += `<label>seed (sensor value)</label><input id="p_seed" value="${o.seed ?? ""}">`;
  box.innerHTML = h;
  const idEl = document.getElementById("p_id");
  idEl.onchange = () => renameNode(selected, idEl.value.trim());
  const ipEl = document.getElementById("p_ip");
  if (ipEl) ipEl.onchange = () => { o.ip = ipEl.value.trim();
    cfg.links.forEach(l => { if (l.a === o.id) l.a_ip = o.ip; if (l.b === o.id) l.b_ip = o.ip; }); render(); };
  const seedEl = document.getElementById("p_seed");
  if (seedEl) seedEl.onchange = () => { o.seed = parseFloat(seedEl.value); };
}
function renameNode(oldId, newId) {
  if (!newId || newId === oldId || allNodes().some(n => n.id === newId)) { render(); renderProps(); return; }
  const o = nodeObj(oldId); o.id = newId;
  cfg.links.forEach(l => { if (l.a === oldId) l.a = newId; if (l.b === oldId) l.b = newId; });
  cfg.mapping.forEach(m => { if (m.device === oldId) m.device = newId; });
  pos[newId] = pos[oldId]; delete pos[oldId];
  if (selected === oldId) selected = newId;
  render(); renderProps();
}

// ---- drag + click -----------------------------------------------------------
let drag = null;
svg.addEventListener("mousedown", e => {
  const g = e.target.closest(".node"); if (!g) { select(null); return; }
  const id = g.getAttribute("data-id");
  if (linkMode) { if (!linkFrom) { linkFrom = id; select(id); } else { addLink(linkFrom, id); linkFrom = null; } return; }
  select(id);
  const p = pos[id]; drag = { id, dx: e.offsetX - p.x, dy: e.offsetY - p.y };
});
svg.addEventListener("mousemove", e => {
  if (!drag) return; pos[drag.id] = { x: snap(e.offsetX - drag.dx), y: snap(e.offsetY - drag.dy) }; render();
});
window.addEventListener("mouseup", () => drag = null);

// ---- toolbar ----------------------------------------------------------------
document.querySelectorAll("[data-add]").forEach(b =>
  b.onclick = () => addNode(b.dataset.add, 60 + Math.random() * 200, 60 + Math.random() * 160));
document.getElementById("linkBtn").onclick = e => {
  linkMode = !linkMode; linkFrom = null;
  e.target.textContent = "Link mode: " + (linkMode ? "on" : "off");
  e.target.classList.toggle("act", linkMode);
};
document.getElementById("delBtn").onclick = () => { if (selected) removeNode(selected); };
document.getElementById("applyRules").onclick = () => { textToRules(document.getElementById("rules").value); log("rules applied: " + cfg.rules.length); };

// ---- import / export (lossless round-trip) ---------------------------------
document.getElementById("exportBtn").onclick = () => {
  syncRules();
  cfg._view = pos;                       // carry positions (ignored by the engine)
  const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = (cfg.name || "network") + ".json"; a.click();
  log("exported " + a.download);
};
document.getElementById("importBtn").onclick = () => document.getElementById("file").click();
document.getElementById("file").onchange = ev => {
  const f = ev.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = () => { loadConfig(JSON.parse(r.result)); log("imported " + f.name); };
  r.readAsText(f);
};
function loadConfig(obj) {
  cfg = Object.assign(blankConfig(), obj);
  pos = cfg._view || {};
  // lay out any node lacking a position
  let i = 0;
  allNodes().forEach(n => { if (!pos[n.id]) { pos[n.id] = { x: 60 + (i % 6) * 110, y: 60 + Math.floor(i / 6) * 90 }; } i++; });
  document.getElementById("rules").value = rulesToText();
  selected = null; render(); renderProps();
}
function syncRules() { textToRules(document.getElementById("rules").value); }

// ---- service calls (shared L1/L2) ------------------------------------------
function base() { return (document.getElementById("base").value || "").replace(/\/$/, ""); }
function headers() {
  const h = { "Content-Type": "application/json" };
  const t = document.getElementById("token").value.trim(); if (t) h["X-SCADAVT-Token"] = t;
  return h;
}
async function post(path, body) {
  const res = await fetch(base() + path, { method: "POST", headers: headers(), body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, data };
}
document.getElementById("validateBtn").onclick = async () => {
  syncRules(); cfg._view = pos;
  const { status, data } = await post("/validate", { config: stripView(cfg) });
  log(data.ok ? "VALID ✓" : "INVALID (" + (data.issues || []).length + "):\n  " + (data.issues || []).join("\n  "));
};
document.getElementById("runBtn").onclick = async () => {
  syncRules();
  const { status, data } = await post("/run", { config: stripView(cfg) });
  if (status === 200) log(`RUN: started=${data.started} reached ${data.reached}/${data.devices} devices`);
  else log(`RUN failed (${status}): ` + (data.detail || JSON.stringify(data)) + ((data.issues) ? "\n  " + data.issues.join("\n  ") : ""));
};
function stripView(c) { const o = Object.assign({}, c); delete o._view; return o; }

function log(s) { const el = document.getElementById("log"); el.textContent = (new Date().toLocaleTimeString()) + "  " + s + "\n" + el.textContent; }

// expose for E2E/testing
window.SCADAVT = { get cfg(){return cfg;}, addNode, addLink, removeNode, loadConfig, textToRules,
                   exportJSON: () => { syncRules(); return JSON.stringify(stripView(cfg)); } };
render();
log("ready — add nodes, wire links, define rules, then Validate / Run.");
