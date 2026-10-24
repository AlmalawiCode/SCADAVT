/* SCADAVT generic network designer — vanilla, no framework, domain-neutral.
 *
 * The in-memory document IS the canonical config (scadavt.core.model), so
 * export/import is plain JSON and a Python<->GUI round-trip is lossless by
 * construction. Every edit is a pure mutation of `cfg`. Topologies are
 * arbitrary. The GUI never validates or runs itself — it calls the shared
 * HTTP service (L1/L2).
 *
 * Interaction model:
 *   - Select mode (default): press-and-drag a node to move it ANYWHERE (free,
 *     no grid snapping); a click (no drag) selects it for editing.
 *   - Connect mode: click node A then node B to wire them; a dashed rubber-band
 *     line follows the cursor; Esc or right-click cancels.
 */
"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
const COLORS = { plc: "var(--plc)", mtu: "var(--mtu)", router: "var(--router)", switch: "var(--switch)" };
const W = 92, H = 38;                      // node box size

let cfg = blankConfig();
let pos = {};                              // id -> {x,y} (presentation only)
let selected = null;
let mode = "select";                       // 'select' | 'connect'
let connectFrom = null;                    // id while wiring
let counter = { plc: 0, mtu: 0, switch: 0, router: 0 };

function blankConfig() {
  return { schema_version: 1, name: "untitled", control: { placement: "mtu" },
           netmask: 24, mtu: null, devices: [], switches: [], routers: [],
           links: [], mapping: [], rules: [] };
}

// ---- model helpers ---------------------------------------------------------
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
function isEndpoint(id) { return ["plc", "mtu", "router"].includes(nodeKind(id)); }

// ---- mutations -------------------------------------------------------------
function addNode(kind, x, y) {
  counter[kind] = (counter[kind] || 0) + 1;
  let id;
  if (kind === "plc") {
    id = "PLC" + counter.plc;
    const i = cfg.devices.length + 1;
    cfg.devices.push({ id, ip: `10.0.1.${cfg.devices.length + 1}`, protocol: "modbus", seed: 2.0,
      points: [{ id: "s", role: "measurement" }, { id: "a", role: "command", is_bool: true }] });
    cfg.mapping.push({ point: "s", device: id, role: "measurement", element: "S" + i });
    cfg.mapping.push({ point: "a", device: id, role: "command", element: "A" + i });
  } else if (kind === "mtu") {
    if (cfg.mtu) { log("There is already an MTU (one MTU controls many PLCs)."); return; }
    id = "MTU"; cfg.mtu = { id, ip: "10.0.1.254" };
  } else if (kind === "switch") { id = "SW" + counter.switch; cfg.switches.push({ id }); }
  else if (kind === "router") { id = "R" + counter.router; cfg.routers.push({ id, model: "router" }); }
  pos[id] = { x: x == null ? 80 + Math.random() * 320 : x, y: y == null ? 70 + Math.random() * 220 : y };
  render(); select(id);
  return id;
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
  if (connectFrom === id) connectFrom = null;
  render(); renderProps();
}

function addLink(a, b) {
  if (!a || !b || a === b) { log("Pick two different nodes to connect."); return false; }
  if (cfg.links.some(l => (l.a === a && l.b === b) || (l.a === b && l.b === a))) {
    log(`${a} and ${b} are already connected.`); return false;
  }
  const link = { a, b };
  const ea = nodeObj(a), eb = nodeObj(b);
  if (nodeKind(a) !== "switch" && ea && ea.ip) link.a_ip = ea.ip;
  if (nodeKind(b) !== "switch" && eb && eb.ip) link.b_ip = eb.ip;
  cfg.links.push(link);
  if (isEndpoint(a) && isEndpoint(b)) {
    log(`Linked ${a} ↔ ${b} (direct point-to-point). Tip: devices usually connect to a Switch.`);
  } else {
    log(`Linked ${a} ↔ ${b}.`);
  }
  render();
  return true;
}

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
    if (!m) { log("Ignored rule (expected `S1 < 3 => A2 open`): " + line); return; }
    rules.push({ id: "r" + (i + 1),
      if: [{ kind: "sensor", comp: m[1], attr: "value", op: "below", value: parseFloat(m[2]) }],
      then: [{ comp: m[3], kind: "pump", attr: "status", value: m[4] || "open" }] });
  });
  cfg.rules = rules;
}

// ---- rendering --------------------------------------------------------------
const svg = document.getElementById("canvas");
let rubber = null;
function center(id) { const p = pos[id]; return { x: p.x + W / 2, y: p.y + H / 2 }; }
function render() {
  svg.innerHTML = "";
  cfg.links.forEach(l => {
    const pa = pos[l.a], pb = pos[l.b]; if (!pa || !pb) return;
    const ca = center(l.a), cb = center(l.b);
    const p2p = isEndpoint(l.a) && isEndpoint(l.b);
    svg.appendChild(el("line", { class: "link" + (p2p ? " p2p" : ""), x1: ca.x, y1: ca.y, x2: cb.x, y2: cb.y }));
  });
  if (rubber) svg.appendChild(el("line", { class: "rubber", x1: rubber.x1, y1: rubber.y1, x2: rubber.x2, y2: rubber.y2 }));
  allNodes().forEach(n => {
    const p = pos[n.id] || (pos[n.id] = { x: 60, y: 60 });
    const g = el("g", { class: "node", "data-id": n.id, transform: `translate(${p.x},${p.y})` });
    if (selected === n.id || connectFrom === n.id)
      g.appendChild(el("rect", { class: "halo", x: -4, y: -4, width: W + 8, height: H + 8, rx: 11 }));
    g.appendChild(el("rect", { width: W, height: H, rx: 9, fill: COLORS[n.kind] || "#888" }));
    g.appendChild(txt(n.id, W / 2, 17, "text"));
    const o = nodeObj(n.id);
    g.appendChild(txt((o && o.ip) || n.kind, W / 2, 30, "lab"));
    svg.appendChild(g);
  });
}
function el(t, a) { const e = document.createElementNS(SVGNS, t); for (const k in a) e.setAttribute(k, a[k]); return e; }
function txt(s, x, y, cls) { const e = el("text", { x, y, class: cls, "text-anchor": "middle" }); e.textContent = s; return e; }

// ---- properties -------------------------------------------------------------
function select(id) { selected = id; render(); renderProps(); }
function renderProps() {
  const box = document.getElementById("props");
  if (!selected || !nodeObj(selected)) { box.innerHTML = '<p class="hint">Click a node to edit its id / IP / seed.</p>'; return; }
  const o = nodeObj(selected), kind = nodeKind(selected);
  let h = `<div style="display:inline-block;padding:2px 8px;border-radius:999px;color:#06101f;background:${COLORS[kind]}">${kind}</div>`;
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
  nodeObj(oldId).id = newId;
  cfg.links.forEach(l => { if (l.a === oldId) l.a = newId; if (l.b === oldId) l.b = newId; });
  cfg.mapping.forEach(m => { if (m.device === oldId) m.device = newId; });
  pos[newId] = pos[oldId]; delete pos[oldId];
  if (selected === oldId) selected = newId;
  render(); renderProps();
}

// ---- pointer interaction ----------------------------------------------------
function svgXY(e) { const r = svg.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
let drag = null, connectDragged = false;
svg.addEventListener("mousedown", e => {
  if (e.button === 2) return;                         // right-click handled on contextmenu
  const g = e.target.closest(".node");
  const m = svgXY(e);
  if (mode === "connect") {
    if (!g) { connectFrom = null; rubber = null; render(); updateStatus(); return; }
    const id = g.getAttribute("data-id");
    if (connectFrom && id !== connectFrom) {            // click-click: second node completes
      addLink(connectFrom, id); connectFrom = null; rubber = null; render(); updateStatus(); return;
    }
    connectFrom = id; connectDragged = false;           // arm (works for click-click OR drag)
    const c = center(id); rubber = { x1: c.x, y1: c.y, x2: c.x, y2: c.y };
    select(id); updateStatus(); return;
  }
  if (!g) { select(null); return; }
  const id = g.getAttribute("data-id");
  drag = { id, ox: m.x - pos[id].x, oy: m.y - pos[id].y, moved: false };
  g.classList.add("grabbing");
});
svg.addEventListener("mousemove", e => {
  const m = svgXY(e);
  if (mode === "connect" && connectFrom) {
    connectDragged = true;
    const c = center(connectFrom); rubber = { x1: c.x, y1: c.y, x2: m.x, y2: m.y }; render();
    return;
  }
  if (!drag) return;
  drag.moved = true;
  pos[drag.id] = { x: m.x - drag.ox, y: m.y - drag.oy };     // free movement, no snap
  render();
});
window.addEventListener("mouseup", e => {
  if (mode === "connect" && connectFrom && connectDragged) {  // drag-to-connect: release on target
    const t = document.elementFromPoint(e.clientX, e.clientY);
    const g = t && t.closest && t.closest(".node");
    const id = g && g.getAttribute("data-id");
    if (id && id !== connectFrom) addLink(connectFrom, id);
    connectFrom = null; rubber = null; connectDragged = false; render(); updateStatus();
    return;
  }
  if (drag && !drag.moved) select(drag.id);                  // a click (not a drag) selects
  drag = null;
});
svg.addEventListener("contextmenu", e => {                   // right-click cancels wiring
  if (mode === "connect" && connectFrom) { e.preventDefault(); connectFrom = null; rubber = null; render(); updateStatus(); }
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { connectFrom = null; rubber = null; if (mode === "connect") setMode("select"); render(); updateStatus(); closeHelp(); }
  if ((e.key === "Delete" || e.key === "Backspace") && selected && document.activeElement.tagName !== "INPUT"
      && document.activeElement.tagName !== "TEXTAREA") { removeNode(selected); }
});

// ---- modes + status ---------------------------------------------------------
function setMode(m) {
  mode = m; connectFrom = null; rubber = null;
  document.getElementById("connectBtn").classList.toggle("act", m === "connect");
  render(); updateStatus();
}
function updateStatus() {
  const s = document.getElementById("status");
  if (mode !== "connect") s.innerHTML = 'Mode: <b>Select</b> — drag nodes to move; click <b>🔗 Connect</b> to wire them.';
  else if (!connectFrom) s.innerHTML = 'Mode: <b>Connect</b> — click a node (or drag from one node onto another) to wire them. (Esc to exit)';
  else s.innerHTML = `Mode: <b>Connect</b> — now click the SECOND node to link <b>${connectFrom}</b>. (right-click / Esc to cancel)`;
}

// ---- toolbar ---------------------------------------------------------------
document.querySelectorAll("[data-add]").forEach(b => b.onclick = () => addNode(b.dataset.add));
document.getElementById("connectBtn").onclick = () => setMode(mode === "connect" ? "select" : "connect");
document.getElementById("delBtn").onclick = () => { if (selected) removeNode(selected); else log("Select a node first, then Delete."); };
document.getElementById("applyRules").onclick = () => { textToRules(document.getElementById("rules").value); log("Applied " + cfg.rules.length + " rule(s)."); };

// ---- import / export -------------------------------------------------------
function syncRules() { textToRules(document.getElementById("rules").value); }
function stripView(c) { const o = Object.assign({}, c); delete o._view; return o; }
document.getElementById("exportBtn").onclick = () => {
  syncRules(); cfg._view = pos;
  const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = (cfg.name || "network") + ".json"; a.click();
  log("Exported " + a.download);
};
document.getElementById("importBtn").onclick = () => document.getElementById("file").click();
document.getElementById("file").onchange = ev => {
  const f = ev.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = () => { try { loadConfig(JSON.parse(r.result)); log("Imported " + f.name); } catch (e) { log("Import failed: " + e.message); } };
  r.readAsText(f);
};
function loadConfig(obj) {
  cfg = Object.assign(blankConfig(), obj);
  pos = cfg._view || {};
  let i = 0;
  allNodes().forEach(n => { if (!pos[n.id]) pos[n.id] = { x: 70 + (i % 6) * 120, y: 70 + Math.floor(i / 6) * 100 }; i++; });
  // keep the id counters ahead of imported names
  allNodes().forEach(n => { const m = (n.id.match(/\d+$/) || [])[0]; if (m) counter[n.kind] = Math.max(counter[n.kind] || 0, +m); });
  document.getElementById("rules").value = rulesToText();
  selected = null; setMode("select");
}

// ---- service calls ---------------------------------------------------------
function base() { return (document.getElementById("base").value || "").replace(/\/$/, ""); }
function headers() {
  const h = { "Content-Type": "application/json" };
  const t = document.getElementById("token").value.trim(); if (t) h["X-SCADAVT-Token"] = t;
  return h;
}
async function post(path, body) {
  try {
    const res = await fetch(base() + path, { method: "POST", headers: headers(), body: JSON.stringify(body) });
    return { status: res.status, data: await res.json().catch(() => ({})) };
  } catch (e) { return { status: 0, data: { detail: "cannot reach service: " + e.message } }; }
}
document.getElementById("validateBtn").onclick = async () => {
  syncRules();
  const { status, data } = await post("/validate", { config: stripView(cfg) });
  if (status === 0) return log(data.detail);
  log(data.ok ? "VALID ✓" : "INVALID (" + (data.issues || []).length + "):\n  " + (data.issues || []).join("\n  "));
};
document.getElementById("runBtn").onclick = async () => {
  syncRules(); log("Running on CORE…");
  const { status, data } = await post("/run", { config: stripView(cfg) });
  if (status !== 200) return log(`RUN failed (${status}): ` + (data.detail || "") + ((data.issues) ? "\n  " + data.issues.join("\n  ") : ""));
  let s = `RUN ✓ started=${data.started}, reached ${data.reached}/${data.devices}`;
  (data.results || []).forEach(r => { s += `\n  ${r.id}: reachable=${r.reachable} ` + Object.entries(r.points || {}).map(([k, v]) => `${k}=${v}`).join(" "); });
  if ((cfg.rules || []).length === 0)
    s += "\n  note: no rules applied, so actuators (a) stay at default — add rules then Apply.";
  if ((data.results || []).some(r => !r.reachable))
    s += "\n  ⚠ some devices unreachable — connect every device (usually via a Switch) and give them IPs in the same subnet.";
  log(s);
};

// ---- tutorial (opens the full guide page in a new tab) ---------------------
document.getElementById("helpBtn").onclick = () => window.open("tutorial.html", "_blank", "noopener");

function log(s) { const e = document.getElementById("log"); e.textContent = new Date().toLocaleTimeString() + "  " + s + "\n" + e.textContent; }

// expose for E2E/testing
window.SCADAVT = { get cfg() { return cfg; }, addNode, addLink, removeNode, loadConfig, textToRules,
                   exportJSON: () => { syncRules(); return JSON.stringify(stripView(cfg)); } };

setMode("select"); render(); updateStatus();
log("Ready. Click “? Tutorial” (opens a full guide with a 3-PLC example in a new tab).");
