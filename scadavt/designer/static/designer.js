/* SCADAVT generic network designer — vanilla, no framework, domain-neutral.
 *
 * The in-memory document IS the canonical config (scadavt.core.model), so
 * export/import is plain JSON and a Python<->GUI round-trip is lossless by
 * construction. Every edit is a pure mutation of `cfg`. Topologies are
 * arbitrary. The GUI never validates or runs itself — it calls the shared
 * HTTP service (L1/L2). The I/O catalog and each channel's Modbus address also
 * come from the service (/io/catalog, /io), so the GUI never allocates addresses.
 *
 * Interaction model:
 *   - Select mode (default): press-and-drag a node to move it ANYWHERE (free,
 *     no grid snapping); a click (no drag) selects it for editing.
 *   - Connect mode: click node A then node B to wire them; a dashed rubber-band
 *     line follows the cursor; Esc or right-click cancels.
 *   - A selected PLC shows its I/O modules and channels; each channel can be
 *     wired to an emulated sensor (inputs) or actuator (outputs).
 */
"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
const COLORS = { plc: "var(--plc)", mtu: "var(--mtu)", router: "var(--router)", switch: "var(--switch)" };
const W = 92, H = 38;                      // node box size
// a new PLC's I/O: a compact controller's mix (analog in, digital in/out)
const DEFAULT_IO = [{ type: "AI", channels: 4 }, { type: "DI", channels: 4 }, { type: "DO", channels: 4 }];
// instrument-style tag prefixes for emulated field devices
const TAG = { temperature: "TT", humidity: "HT", pressure: "PT", level: "LT", flow: "FT", analog: "AT",
              switch: "HS", pump: "P", fan: "FAN", valve: "XV", lamp: "L", motor: "M",
              valve_position: "FV", speed: "SC" };
const OPS = { "<": "below", ">": "above", "<=": "at_most", ">=": "at_least", "==": "equal", "!=": "not_equal" };

let cfg = blankConfig();
let pos = {};                              // id -> {x,y} (presentation only)
let selected = null;
let mode = "select";                       // 'select' | 'connect'
let connectFrom = null;                    // id while wiring
let counter = { plc: 0, mtu: 0, switch: 0, router: 0 };
let catalog = null;                        // /io/catalog: module + field-device types
let ioTable = {};                          // device id -> channel rows from /io

function blankConfig() {
  return { schema_version: 1, name: "untitled", control: { placement: "mtu" },
           netmask: 24, mtu: null, devices: [], switches: [], routers: [],
           links: [], logic: [], sensors: [], actuators: [] };
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
function fieldDevices() { return [...(cfg.sensors || []), ...(cfg.actuators || [])]; }
function wiredTo(dev, ch) { return fieldDevices().find(f => f.device === dev && f.channel === ch); }

// ---- mutations -------------------------------------------------------------
function addNode(kind, x, y) {
  counter[kind] = (counter[kind] || 0) + 1;
  let id;
  if (kind === "plc") {
    id = "PLC" + counter.plc;
    // a PLC is a Modbus device with I/O modules; each channel is a register
    cfg.devices.push({ id, ip: `10.0.1.${cfg.devices.length + 1}`, protocol: "modbus",
      io_modules: DEFAULT_IO.map(m => Object.assign({}, m)) });
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
  cfg.logic = (cfg.logic || []).filter(r =>
    !(r.if || []).some(c => c.device === id) && !(r.then || []).some(a => a.device === id));
  cfg.sensors = (cfg.sensors || []).filter(f => f.device !== id);
  cfg.actuators = (cfg.actuators || []).filter(f => f.device !== id);
  delete pos[id]; delete ioTable[id];
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

// ---- I/O modules + emulated field devices ----------------------------------
// Channel naming rule of scadavt.core.fieldio.channels (per type, module order).
// Used only to re-point wiring when a module is removed; addresses come from /io.
function chanIds(dev) {
  const n = {}, out = [];
  (dev.io_modules || []).forEach((m, slot) => {
    for (let k = 0; k < (m.channels | 0); k++) {
      const i = n[m.type] || 0; n[m.type] = i + 1; out.push({ id: m.type + i, slot });
    }
  });
  return out;
}
function addModule(devId, type, channels) {
  const dev = nodeObj(devId);
  (dev.io_modules = dev.io_modules || []).push({ type, channels });
  log(`${devId}: added a ${type} module with ${channels} channel(s).`);
  renderProps();
}
function removeModule(devId, slot) {
  const dev = nodeObj(devId), before = chanIds(dev);
  dev.io_modules.splice(slot, 1);
  const after = chanIds(dev);
  // channels after the removed module shift down: map old -> new ids
  const map = {};
  before.filter(c => c.slot !== slot).forEach((c, i) => { map[c.id] = after[i].id; });
  const gone = new Set(before.filter(c => c.slot === slot).map(c => c.id));
  ["sensors", "actuators"].forEach(k => {
    cfg[k] = (cfg[k] || []).filter(f => !(f.device === devId && gone.has(f.channel)));
    cfg[k].forEach(f => { if (f.device === devId && map[f.channel]) f.channel = map[f.channel]; });
  });
  // a rule on a removed channel must go too — its name may now belong to a
  // shifted channel, which would silently change what the rule means
  const uses = r => [...(r.if || []), ...(r.then || [])].some(c => c.device === devId && gone.has(c.register));
  const dropped = (cfg.logic || []).filter(uses);
  cfg.logic = (cfg.logic || []).filter(r => !uses(r));
  cfg.logic.forEach(r => [...(r.if || []), ...(r.then || [])].forEach(c => {
    if (c.device === devId && map[c.register]) c.register = map[c.register];
  }));
  document.getElementById("rules").value = rulesToText();
  log(`${devId}: removed module in slot ${slot + 1}.` +
      (dropped.length ? ` Also removed ${dropped.length} rule(s) that used its channels.` : ""));
  render(); renderProps();
}
function newTag(type) {
  const p = TAG[type] || type.slice(0, 2).toUpperCase();
  const used = new Set(fieldDevices().map(f => f.id));
  let i = 1; while (used.has(p + i)) i++;
  return p + i;
}
function wire(devId, ch, type) {
  // one field device per channel: replace whatever was there
  cfg.sensors = (cfg.sensors || []).filter(f => !(f.device === devId && f.channel === ch));
  cfg.actuators = (cfg.actuators || []).filter(f => !(f.device === devId && f.channel === ch));
  if (type) {
    const sensor = catalog && catalog.sensors[type];
    const f = { id: newTag(type), type, device: devId, channel: ch };
    if (sensor) ["wave", "min", "max", "value", "period", "duty"].forEach(k => {
      if (sensor[k] !== undefined) f[k] = sensor[k];
    });
    (sensor ? cfg.sensors : cfg.actuators).push(f);
    log(`Wired ${f.id} (${type}) to ${devId}.${ch}.`);
  }
  render(); renderProps();
}

// ---- rules text <-> cfg.logic ----------------------------------------------
function rulesToText() {
  const sym = Object.fromEntries(Object.entries(OPS).map(([k, v]) => [v, k]));
  return (cfg.logic || []).map(r => {
    const c = (r.if && r.if[0]) || {}, t = (r.then && r.then[0]) || {};
    const set = (t.set !== undefined ? t.set : t.value);
    const act = set === 1 ? "on" : set === 0 ? "off" : String(set);
    return `${c.device}.${c.register} ${sym[c.op] || "<"} ${c.value} => ${t.device}.${t.register} ${act}`;
  }).join("\n");
}
function textToRules(text) {
  // "PLC1.AI0 > 25 => PLC2.DO0 on"  (also <, <=, >=, ==, !=; on/off/open/close or a number)
  const logic = [];
  text.split("\n").map(s => s.trim()).filter(Boolean).forEach((line, i) => {
    const m = line.match(/^(\w+)\.(\w+)\s*(<=|>=|==|!=|<|>)\s*(-?[\d.]+)\s*=>\s*(\w+)\.(\w+)\s*(\S+)?$/);
    if (!m) { log("Ignored rule (expected `PLC1.AI0 > 25 => PLC2.DO0 on`): " + line); return; }
    const a = (m[7] || "on").toLowerCase();
    const set = ["on", "open", "true"].includes(a) ? 1 : ["off", "close", "false"].includes(a) ? 0 : parseFloat(a);
    if (Number.isNaN(set)) { log("Ignored rule (action must be on/off or a number): " + line); return; }
    logic.push({ id: "r" + (i + 1),
      if: [{ device: m[1], register: m[2], op: OPS[m[3]], value: parseFloat(m[4]) }],
      then: [{ device: m[5], register: m[6], set }] });
  });
  cfg.logic = logic;
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
    // the emulated field devices wired into this PLC's I/O, under the box
    const tags = fieldDevices().filter(f => f.device === n.id).map(f => `${f.id}→${f.channel}`);
    tags.forEach((t, i) => g.appendChild(txt(t, W / 2, H + 17 + i * 12, "tag")));
    svg.appendChild(g);
  });
}
function el(t, a) { const e = document.createElementNS(SVGNS, t); for (const k in a) e.setAttribute(k, a[k]); return e; }
function txt(s, x, y, cls) { const e = el("text", { x, y, class: cls, "text-anchor": "middle" }); e.textContent = s; return e; }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---- properties -------------------------------------------------------------
function select(id) { selected = id; render(); renderProps(); }
function renderProps() {
  const box = document.getElementById("props");
  if (!selected || !nodeObj(selected)) { box.innerHTML = '<p class="hint">Click a node to edit it. Click a PLC to see its I/O modules and wire sensors.</p>'; return; }
  const o = nodeObj(selected), kind = nodeKind(selected);
  let h = `<div style="display:inline-block;padding:2px 8px;border-radius:999px;color:#06101f;background:${COLORS[kind]}">${kind}</div>`;
  h += `<label>id</label><input id="p_id" value="${esc(o.id)}">`;
  if (kind !== "switch") h += `<label>ip</label><input id="p_ip" value="${esc(o.ip || "")}">`;
  if (kind === "plc") {
    if (o.seed !== undefined || (o.registers || []).length) {   // plain-register PLC (imported)
      const regs = (o.registers || []).map(r => `${r.id}:${r.area}`).join(", ");
      h += `<label>seed (input register 's' initial value)</label><input id="p_seed" value="${o.seed ?? ""}">`;
      h += `<label>registers</label><div class="hint">${esc(regs)}</div>`;
    }
    h += `<h4>I/O modules</h4>`;
    (o.io_modules || []).forEach((m, i) => {
      const lab = catalog ? catalog.modules[m.type].label : m.type;
      h += `<div class="mod"><span>Slot ${i + 1} · <b>${m.type}</b> · ${m.channels} ch <span class="hint">${esc(lab)}</span></span>`
         + `<button data-rm="${i}" title="Remove this module">✕</button></div>`;
    });
    if (!(o.io_modules || []).length) h += `<p class="hint">No I/O modules — add one below.</p>`;
    h += `<div class="row"><select id="m_type"><option>AI</option><option>AO</option><option>DI</option><option>DO</option></select>`
       + `<input id="m_n" type="number" min="1" max="16" value="4" title="channels"><button id="m_add">+ Module</button></div>`;
    h += `<h4>Channels → Modbus</h4><div id="chans" class="hint">loading…</div>`;
  }
  box.innerHTML = h;
  const idEl = document.getElementById("p_id");
  idEl.onchange = () => renameNode(selected, idEl.value.trim());
  const ipEl = document.getElementById("p_ip");
  if (ipEl) ipEl.onchange = () => { o.ip = ipEl.value.trim();
    cfg.links.forEach(l => { if (l.a === o.id) l.a_ip = o.ip; if (l.b === o.id) l.b_ip = o.ip; }); render(); };
  const seedEl = document.getElementById("p_seed");
  if (seedEl) seedEl.onchange = () => {
    o.seed = parseFloat(seedEl.value);
    const sreg = (o.registers || []).find(r => r.id === "s");   // seed = input register 's' initial
    if (sreg) sreg.initial = o.seed;
  };
  if (kind === "plc") {
    box.querySelectorAll("[data-rm]").forEach(b => b.onclick = () => removeModule(o.id, +b.dataset.rm));
    document.getElementById("m_add").onclick = () => {
      const n = Math.max(1, Math.min(16, parseInt(document.getElementById("m_n").value, 10) || 1));
      addModule(o.id, document.getElementById("m_type").value, n);
    };
    refreshIO();
  }
}
function renderChannels() {
  const box = document.getElementById("chans");
  if (!box || !selected) return;
  const rows = ioTable[selected];
  if (!rows || !rows.length) { box.innerHTML = '<span class="hint">No channels.</span>'; return; }
  if (rows[0].error) { box.innerHTML = `<span class="hint">${esc(rows[0].error)}</span>`; return; }
  let h = `<table class="ch"><tr><th>Ch</th><th>Modbus</th><th>Wired to</th></tr>`;
  rows.forEach(r => {
    const f = wiredTo(selected, r.channel);
    const input = r.type === "AI" || r.type === "DI";
    const pool = catalog ? (input ? catalog.sensors : catalog.actuators) : {};
    const opts = Object.keys(pool).filter(t => pool[t].module === r.type)
      .map(t => `<option value="${t}"${f && f.type === t ? " selected" : ""}>${t}${pool[t].unit ? " (" + pool[t].unit + ")" : ""}</option>`).join("");
    h += `<tr><td title="slot ${r.slot}">${r.channel}</td>`
       + `<td title="${esc(r.table)} @ ${r.address}, ${esc(r.access)}">${String(r.ref).padStart(5, "0")}</td>`
       + `<td><select data-ch="${r.channel}"><option value="">— ${input ? "no sensor" : "no actuator"} —</option>${opts}</select></td></tr>`;
    if (f) {
      h += `<tr class="sub"><td colspan="3">tag <input data-f="${f.id}" data-k="id" value="${esc(f.id)}">`;
      if (input) {
        const waves = (catalog ? catalog.waves : []).map(w => `<option${(f.wave || "constant") === w ? " selected" : ""}>${w}</option>`).join("");
        h += ` <select data-f="${f.id}" data-k="wave">${waves}</select>`;
        if ((f.wave || "constant") === "constant") h += ` value <input data-f="${f.id}" data-k="value" value="${f.value ?? 0}">`;
        else h += ` <input data-f="${f.id}" data-k="min" value="${f.min ?? 0}" title="min">–<input data-f="${f.id}" data-k="max" value="${f.max ?? 1}" title="max">`;
      } else h += ` <span class="hint">receives the MTU's command</span>`;
      h += `</td></tr>`;
    }
  });
  box.innerHTML = h + `</table>`;
  box.querySelectorAll("[data-ch]").forEach(s => s.onchange = () => wire(selected, s.dataset.ch, s.value));
  box.querySelectorAll("[data-f]").forEach(e => e.onchange = () => {
    const f = fieldDevices().find(x => x.id === e.dataset.f); if (!f) return;
    const k = e.dataset.k, v = e.value.trim();
    if (k === "id") { if (v && !fieldDevices().some(x => x.id === v)) f.id = v; }
    else if (k === "wave") {
      f.wave = v;
      if (v === "constant" && f.value === undefined) f.value = f.min ?? 0;   // a constant needs a value
    }
    else if (!Number.isNaN(parseFloat(v))) f[k] = parseFloat(v);
    render(); renderProps();
  });
}
async function refreshIO() {
  const { status, data } = await post("/io", { config: stripView(cfg) });
  if (status !== 200) { const b = document.getElementById("chans"); if (b) b.textContent = "(service unreachable — cannot compute Modbus addresses)"; return; }
  ioTable = data.devices || {};
  renderChannels();
}
function renameNode(oldId, newId) {
  if (!newId || newId === oldId || allNodes().some(n => n.id === newId)) { render(); renderProps(); return; }
  nodeObj(oldId).id = newId;
  cfg.links.forEach(l => { if (l.a === oldId) l.a = newId; if (l.b === oldId) l.b = newId; });
  (cfg.logic || []).forEach(r => {
    (r.if || []).forEach(c => { if (c.device === oldId) c.device = newId; });
    (r.then || []).forEach(a => { if (a.device === oldId) a.device = newId; });
  });
  fieldDevices().forEach(f => { if (f.device === oldId) f.device = newId; });
  document.getElementById("rules").value = rulesToText();
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
  if (e.key === "Escape") { connectFrom = null; rubber = null; if (mode === "connect") setMode("select"); render(); updateStatus(); }
  const t = document.activeElement.tagName;
  if ((e.key === "Delete" || e.key === "Backspace") && selected && !["INPUT", "TEXTAREA", "SELECT"].includes(t)) {
    removeNode(selected);
  }
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
document.getElementById("applyRules").onclick = () => { textToRules(document.getElementById("rules").value); log("Applied " + cfg.logic.length + " rule(s)."); };

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
  ioTable = {};
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
async function loadCatalog() {
  try { catalog = await (await fetch(base() + "/io/catalog", { headers: headers() })).json(); }
  catch (e) { log("Could not load the I/O catalog from the service: " + e.message); }
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
  (data.results || []).forEach(r => {
    const labels = r.labels || {};
    if (!(r.io || Object.keys(labels).length)) {           // plain registers: one line
      s += `\n  ${r.id}: reachable=${r.reachable} ` + Object.entries(r.points || {}).map(([k, v]) => `${k}=${v}`).join(" ");
      return;
    }
    s += `\n  ${r.id}: reachable=${r.reachable}`;          // I/O channels: one per line
    Object.entries(r.points || {}).forEach(([k, v]) => {
      const val = v === null || v === undefined ? "--" : (+v).toFixed(2);
      s += `\n     ${k.padEnd(4)} = ${val.padEnd(7)} ${labels[k] || (val === "--" ? "(no signal)" : "")}`;
    });
  });
  if ((cfg.logic || []).length === 0)
    s += "\n  note: no rules applied, so outputs stay off — add rules then Apply.";
  if ((data.results || []).some(r => !r.reachable))
    s += "\n  ⚠ some devices unreachable — connect every device (usually via a Switch) and give them IPs in the same subnet.";
  log(s);
};

// ---- tutorial (opens the full guide page in a new tab) ---------------------
document.getElementById("helpBtn").onclick = () => window.open("tutorial.html", "_blank", "noopener");

function log(s) { const e = document.getElementById("log"); e.textContent = new Date().toLocaleTimeString() + "  " + s + "\n" + e.textContent; }

// expose for E2E/testing
window.SCADAVT = { get cfg() { return cfg; }, get ioTable() { return ioTable; }, addNode, addLink, removeNode,
                   loadConfig, textToRules, addModule, removeModule, wire, refreshIO,
                   exportJSON: () => { syncRules(); return JSON.stringify(stripView(cfg)); } };

setMode("select"); render(); updateStatus();
loadCatalog();
log("Ready. Click “? Tutorial” (opens a full guide with worked examples in a new tab).");
