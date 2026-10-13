"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}) => {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
};

let TOPO = null;        // topology document
let XY = {};            // node id -> {x, y} in SVG space (y already flipped)
let REF = {};           // element id -> {level?, pump?, valve?} DOM nodes
let SELECTED = null;    // selected element id
let CHART = { h: [], T: {} };  // time series
let MAXL = 7, CRIT = 1.05;
let WS = null;
let EDIT = false;       // layout-edit mode
let SCEN_PATH = null;   // writable workspace scenario path (when editing)
let MOVED = new Set();  // node ids whose coordinates changed
let ATTACKS = [];       // composed attack specs
let LINKS = {};         // link id -> {line, kind} for flow animation
let LIVE = false;       // is the plant currently running (drives animation)

function setLive(on) {
  LIVE = on;
  const net = $("net"); if (net) net.classList.toggle("live", on);
}
// set a link's flowing-water speed/direction from its flow value (L/min)
function animateLink(line, flow) {
  if (!line) return;
  if (flow == null) { line.style.animationDuration = "3s"; line.style.opacity = "0.5"; return; }
  const mag = Math.min(1, Math.abs(flow) / 3000);
  line.style.animationDuration = (2.6 - 2.1 * mag).toFixed(2) + "s";
  line.style.animationDirection = flow < 0 ? "reverse" : "normal";
  line.style.opacity = (0.35 + 0.6 * mag).toFixed(2);
}

async function loadTopology() {
  const url = SCEN_PATH ? `api/scenario?path=${encodeURIComponent(SCEN_PATH)}` : "api/scenario";
  const r = await fetch(url);
  TOPO = await r.json();
  if (TOPO.error) { $("run-msg").textContent = TOPO.error; return false; }
  $("scenario-name").value = TOPO.scenario;
  const maxv = Object.values(TOPO.tank_max_level || {});
  if (maxv.length) MAXL = Math.max(...maxv);
  CRIT = 0.15 * MAXL;
  drawNetwork();
  return true;
}

async function init() {
  if (!await loadTopology()) return;
  await refreshStatus();
  connectWS();
  $("btn-start").onclick = startRun;
  $("btn-stop").onclick = stopRun;
  $("edit-mode").onchange = toggleEdit;
  $("btn-save-layout").onclick = saveLayout;
  $("btn-atk-add").onclick = addAttack;
  $("btn-atk-export").onclick = exportExperiment;
  $("btn-atk-run").onclick = runAttack;
  $("btn-atk-stop").onclick = () => fetch("api/attacks/run/stop", { method: "POST" });
  $("btn-atk-dataset").onclick = exportDataset;
  $("btn-results").onclick = loadResults;
  connectAttackWS();
  loadResults();
  checkSystem();
}

// --------------------------------------------------------- system readiness
async function checkSystem() {
  let s;
  try { s = await (await fetch("api/system")).json(); } catch { return; }
  const b = $("sysbanner");
  if (!s.docker) {
    b.className = "banner err"; b.style.display = "flex";
    b.innerHTML = "Docker not detected on the host — the live viewer, authoring and attack builder work, but running attacks on the testbed is unavailable.";
  } else if (!s.images_ready) {
    b.className = "banner"; b.style.display = "flex";
    b.innerHTML = `Testbed images are not built yet. <button id="btn-build">Build images</button> <span id="build-step" class="dim"></span>`;
    $("btn-build").onclick = buildImages;
  } else {
    b.style.display = "none";
  }
}

async function buildImages() {
  const step = $("build-step"); const btn = $("btn-build");
  btn.disabled = true; step.textContent = "starting build…";
  await fetch("api/system/build", { method: "POST" });
  const poll = setInterval(async () => {
    const st = await (await fetch("api/system/build")).json();
    step.textContent = st.step || st.state;
    if (st.state === "done") { clearInterval(poll); checkSystem(); }
    else if (st.state === "error") { clearInterval(poll); btn.disabled = false; step.textContent = "build failed: " + st.step; }
  }, 2000);
}

// --------------------------------------------------- live attack chart
let ATKSERIES = { h: [], tru: [], seen: [], act: [] };
function drawAtkChart() {
  const c = $("atk-chart");
  const H = c.clientHeight || 96, W = c.clientWidth || 280, pad = 6;
  c.setAttribute("viewBox", `0 0 ${W} ${H}`);
  c.innerHTML = "";
  const hs = ATKSERIES.h; if (hs.length < 2) return;
  const vals = [...ATKSERIES.tru, ...ATKSERIES.seen].filter((v) => v != null);
  if (!vals.length) return;
  const vmin = Math.min(...vals), vmax = Math.max(...vals) || 1;
  const span = (vmax - vmin) || 1;
  const h0 = hs[0], h1 = hs[hs.length - 1] || h0 + 1;
  const sx = (h) => pad + (W - 2 * pad) * (h - h0) / Math.max(1e-6, h1 - h0);
  const sy = (v) => H - pad - (H - 2 * pad) * (v - vmin) / span;
  // shade attack-active spans
  for (let i = 1; i < hs.length; i++) if (ATKSERIES.act[i]) {
    c.appendChild(el("rect", { x: sx(hs[i - 1]), y: 0, width: Math.max(1, sx(hs[i]) - sx(hs[i - 1])), height: H, fill: "#ef4444", opacity: 0.12 }));
  }
  const line = (arr, color, dash) => {
    let d = "";
    for (let k = 0; k < hs.length; k++) { const v = arr[k]; if (v == null) continue; d += (d ? "L" : "M") + sx(hs[k]) + " " + sy(v); }
    const p = el("path", { d, fill: "none", stroke: color, "stroke-width": 1.6 });
    if (dash) p.setAttribute("stroke-dasharray", "4 3");
    c.appendChild(p);
  };
  line(ATKSERIES.tru, "#60a5fa");       // true (blue)
  line(ATKSERIES.seen, "#ef4444", true); // MTU-seen (red dashed)
}

function svgSpace() {
  const b = TOPO.bbox, m = Math.max((b.xmax - b.xmin), (b.ymax - b.ymin)) * 0.06;
  XY = {};
  for (const n of TOPO.nodes) {
    if (n.x == null) continue;
    XY[n.id] = { x: n.x, y: b.ymin + b.ymax - n.y };  // flip y for screen
  }
  return { x: b.xmin - m, y: b.ymin - m, w: (b.xmax - b.xmin) + 2 * m, h: (b.ymax - b.ymin) + 2 * m };
}

function drawNetwork() {
  const net = $("net");
  net.innerHTML = "";
  const vb = svgSpace();
  net.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
  const R = Math.max(vb.w, vb.h) / 110;  // base radius
  REF = {}; LINKS = {};

  // vertical water gradient for tank fills
  const defs = el("defs");
  const grad = el("linearGradient", { id: "water", x1: "0", y1: "0", x2: "0", y2: "1" });
  grad.appendChild(el("stop", { offset: "0", "stop-color": "#4aa3e0" }));
  grad.appendChild(el("stop", { offset: "1", "stop-color": "#1f5c93" }));
  defs.appendChild(grad);
  net.appendChild(defs);

  // links first (drawn under nodes) — every link carries flowing-water dashes
  for (const lk of TOPO.links) {
    const a = XY[lk.n1], b = XY[lk.n2];
    if (!a || !b) continue;
    // faint base so a still/idle network still reads as pipes
    const w = lk.kind === "pipe" ? R * 0.4 : R * 0.7;
    net.appendChild(el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      stroke: "#2b3a49", "stroke-width": w, "stroke-linecap": "round" }));
    const color = lk.kind === "valve" ? "#58c4d6" : "#5aa9e6";
    const flowLine = el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "flow",
      stroke: color, "stroke-width": w * 0.8, "stroke-linecap": "round", opacity: 0.45 });
    net.appendChild(flowLine);
    LINKS[lk.id] = { line: flowLine, kind: lk.kind };
    if (lk.kind === "pump" || lk.kind === "valve") {
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      let g;
      if (lk.kind === "pump") {
        g = el("circle", { cx: mx, cy: my, r: R * 1.1, fill: "#5b6675", stroke: "#0d1219", "stroke-width": R * 0.2 });
        REF[lk.id] = { pump: g };
      } else {
        g = el("rect", { x: mx - R, y: my - R, width: 2 * R, height: 2 * R, rx: R * 0.3,
          transform: `rotate(45 ${mx} ${my})`, fill: "#f59e0b", stroke: "#0d1219", "stroke-width": R * 0.2 });
        REF[lk.id] = { valve: g };
      }
      g.style.cursor = "pointer";
      g.addEventListener("click", () => select(lk.id));
      net.appendChild(g);
      net.appendChild(label(lk.id, mx, my - R * 1.8));
    }
  }

  // nodes
  for (const n of TOPO.nodes) {
    const p = XY[n.id];
    if (!p) continue;
    if (n.kind === "tank") {
      const s = R * 2.6;
      const box = el("rect", { x: p.x - s, y: p.y - s, width: 2 * s, height: 2 * s, rx: R * 0.4,
        fill: "#0d141c", stroke: "#3a4656", "stroke-width": R * 0.25 });
      const fill = el("rect", { x: p.x - s, y: p.y, width: 2 * s, height: 0, fill: "url(#water)",
        rx: R * 0.3, class: "tank-fill" });
      const surface = el("rect", { x: p.x - s, y: p.y, width: 2 * s, height: R * 0.28,
        fill: "#9bd0f5", class: "tank-surface" });
      const lvl = el("text", { x: p.x, y: p.y + s - R * 0.6, "text-anchor": "middle", class: "lvltext" });
      lvl.setAttribute("font-size", R * 1.5);
      box.style.cursor = fill.style.cursor = "pointer";
      const sel = () => select(n.id);
      box.addEventListener("click", sel); fill.addEventListener("click", sel);
      net.appendChild(box); net.appendChild(fill); net.appendChild(surface); net.appendChild(lvl);
      net.appendChild(label(n.id, p.x, p.y - s - R * 0.6));
      REF[n.id] = { level: fill, surface, lvltext: lvl, tankTop: p.y - s, tankH: 2 * s };
      if (EDIT) { makeDraggable(box, n.id); makeDraggable(fill, n.id); }
    } else if (n.kind === "reservoir") {
      const c = el("circle", { cx: p.x, cy: p.y, r: R * 1.6, fill: "#1f6f5c", stroke: "#0d1219", "stroke-width": R * 0.2 });
      net.appendChild(c);
      net.appendChild(label(n.id, p.x, p.y - R * 2.2));
      if (EDIT) makeDraggable(c, n.id);
    } else {
      const c = el("circle", { cx: p.x, cy: p.y, r: EDIT ? R * 0.9 : R * 0.45, fill: EDIT ? "#8899aa" : "#64748b" });
      net.appendChild(c);
      if (EDIT) makeDraggable(c, n.id);
    }
  }
}

function label(txt, x, y) {
  const t = el("text", { x, y, "text-anchor": "middle", class: "nlabel" });
  t.textContent = txt;
  return t;
}

function screenToEpanet(evt) {
  const svg = $("net");
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  const b = TOPO.bbox;
  return { x: p.x, y: b.ymin + b.ymax - p.y };  // un-flip y back to EPANET space
}

function makeDraggable(elem, id) {
  elem.style.cursor = "move";
  elem.addEventListener("pointerdown", (e) => {
    if (!EDIT) return;
    e.stopPropagation(); e.preventDefault();
    const move = (ev) => {
      const p = screenToEpanet(ev);
      const n = TOPO.nodes.find((n) => n.id === id);
      if (!n) return;
      n.x = p.x; n.y = p.y; MOVED.add(id);
      drawNetwork();
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}

function applyFrame(row) {
  if (!row) return;
  setLive(true);
  // tanks: water rises/falls smoothly, surface sits on top, level shown
  for (const id in REF) {
    const ref = REF[id];
    if (ref.level) {
      const lvl = row[`${id}_level`];
      if (lvl != null) {
        const frac = Math.max(0, Math.min(1, lvl / MAXL));
        const top = ref.tankTop + ref.tankH * (1 - frac);
        ref.level.setAttribute("y", top);
        ref.level.setAttribute("height", ref.tankH * frac);
        ref.level.setAttribute("fill", lvl <= CRIT ? "#c0392b" : "url(#water)");
        if (ref.surface) { ref.surface.setAttribute("y", top - 1); ref.surface.setAttribute("opacity", frac > 0.02 ? 0.9 : 0); }
        if (ref.lvltext) ref.lvltext.textContent = lvl.toFixed(2);
      }
    }
    if (ref.pump) {
      const st = row[`${id}_status`];
      if (st != null) {
        const on = st >= 0.5;
        ref.pump.setAttribute("fill", on ? "#22c55e" : "#5b6675");
        ref.pump.classList.toggle("pump-on", on);
      }
    }
    if (ref.valve) {
      const fl = row[`${id}_flow`];
      if (fl != null) ref.valve.setAttribute("fill", Math.abs(fl) > 1 ? "#58c4d6" : "#3a6670");
    }
  }
  // flowing water in every link, speed/direction from the measured flow
  for (const id in LINKS) {
    const m = LINKS[id];
    const f = (m.kind === "pump" || m.kind === "valve") ? row[`${id}_flow`] : null;
    animateLink(m.line, f);
  }
  // chart series (tanks)
  const h = row.sim_h;
  if (h != null) {
    CHART.h.push(h);
    for (const n of TOPO.nodes) if (n.kind === "tank") {
      (CHART.T[n.id] ||= []).push(row[`${n.id}_level`]);
    }
    if (CHART.h.length > 4000) { CHART.h.shift(); for (const k in CHART.T) CHART.T[k].shift(); }
    drawChart();
    $("simclock").innerHTML = `t = ${h.toFixed(2)} h`;
  }
  if (SELECTED) renderSelection(row);
}

const TANKCOLORS = ["#60a5fa", "#34d399", "#f472b6", "#fbbf24", "#a78bfa", "#f87171"];
function drawChart() {
  const c = $("chart"); c.innerHTML = "";
  const W = c.clientWidth || 600, H = c.clientHeight || 140, pad = 4;
  c.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const hs = CHART.h; if (hs.length < 2) return;
  const h0 = hs[0], h1 = hs[hs.length - 1] || h0 + 1;
  const sx = (h) => pad + (W - 2 * pad) * (h - h0) / Math.max(1e-6, h1 - h0);
  const sy = (v) => H - pad - (H - 2 * pad) * (v / MAXL);
  // critical line
  const cl = el("line", { x1: pad, y1: sy(CRIT), x2: W - pad, y2: sy(CRIT),
    stroke: "#ef4444", "stroke-width": 1, "stroke-dasharray": "4 3", opacity: .7 });
  c.appendChild(cl);
  let i = 0;
  for (const id in CHART.T) {
    const ys = CHART.T[id];
    let d = "";
    for (let k = 0; k < hs.length; k++) {
      const v = ys[k]; if (v == null) continue;
      d += (d ? "L" : "M") + sx(hs[k]) + " " + sy(v);
    }
    c.appendChild(el("path", { d, fill: "none", stroke: TANKCOLORS[i % TANKCOLORS.length], "stroke-width": 1.4 }));
    i++;
  }
}

function select(id) {
  SELECTED = id;
  renderSelection(/* latest */ null);
  const sup = TOPO.supervised[id];   // prefill the attack builder from the click
  if (sup) {
    $("atk-plc").value = sup.plc;
    const k = $("atk-kind").value;
    const want = k === "command" ? "o" : "i";
    const t = sup.tags.find((t) => t.direction === want) || sup.tags[0];
    if (t) $("atk-tag").value = t.name;
  }
}
function renderSelection(row) {
  const id = SELECTED, box = $("sel");
  const sup = TOPO.supervised[id];
  let html = `<div class="selname">${id}</div>`;
  const live = row || lastRow;
  // element live values
  const keys = Object.keys(live || {}).filter(k => k.startsWith(id + "_"));
  if (keys.length) {
    html += "<table>";
    for (const k of keys) {
      const v = live[k];
      html += `<tr><td>${k.slice(id.length + 1)}</td><td>${typeof v === "number" ? v.toFixed(2) : v}</td></tr>`;
    }
    html += "</table>";
  }
  if (sup) {
    html += `<p class="dim" style="margin-top:8px">Supervised by <b style="color:var(--ink)">${sup.plc}</b></p>`;
    html += "<table>";
    for (const t of sup.tags)
      html += `<tr class="tagrow"><td>${t.name}</td><td>${t.dtype}${t.direction === "o" ? " ·cmd" : ""}</td></tr>`;
    html += "</table>";
  }
  if (!keys.length && !sup) html += `<p class="dim">No live data or supervision for this element.</p>`;
  box.innerHTML = html;
}

let lastRow = null;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  WS = new WebSocket(`${proto}://${location.host}/ws/live`);
  WS.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "frame") { lastRow = m.row; applyFrame(m.row); }
    else if (m.type === "status") setState(m);
  };
  WS.onclose = () => setTimeout(connectWS, 1500);
}

function setState(s) {
  const st = $("state");
  setLive(!!s.running);
  if (s.running) { st.textContent = "running"; st.className = "pill run";
    $("btn-start").disabled = true; $("btn-stop").disabled = false; }
  else if (s.state === "finished") { st.textContent = "finished"; st.className = "pill";
    $("btn-start").disabled = false; $("btn-stop").disabled = true; }
  else if (s.error) { st.textContent = "error"; st.className = "pill err";
    $("run-msg").textContent = s.error; $("run-msg").className = "msg err";
    $("btn-start").disabled = false; $("btn-stop").disabled = true; }
  else { st.textContent = s.state || "idle"; st.className = "pill";
    $("btn-start").disabled = false; $("btn-stop").disabled = true; }
}

async function refreshStatus() {
  const s = await (await fetch("api/run")).json();
  setState(s);
}

async function startRun() {
  $("run-msg").textContent = ""; $("run-msg").className = "msg";
  CHART = { h: [], T: {} }; drawChart();
  const body = {
    hours: +$("p-hours").value, seed: +$("p-seed").value,
    step: +$("p-step").value, tick: +$("p-tick").value,
    supervise: $("p-sup").checked, v1_control: $("p-v1").checked,
  };
  const r = await fetch("api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const s = await r.json();
  if (s.error) { $("run-msg").textContent = s.error; $("run-msg").className = "msg err"; }
  else setState(s);
}
async function stopRun() {
  const s = await (await fetch("api/stop", { method: "POST" })).json();
  setState(s);
}

// ------------------------------------------------------------ layout editing
async function toggleEdit(e) {
  EDIT = e.target.checked;
  $("btn-save-layout").disabled = !EDIT;
  const msg = $("layout-msg");
  if (EDIT && !SCEN_PATH) {
    msg.textContent = "preparing a writable workspace…";
    const r = await fetch("api/workspace", { method: "POST" });
    SCEN_PATH = (await r.json()).path;
    await loadTopology();
    msg.textContent = "drag tanks/junctions/reservoirs to reposition, then Save layout.";
  } else {
    drawNetwork();
    msg.textContent = EDIT ? "drag nodes, then Save layout." :
      "editing off. Saved edits persist in the workspace scenario.";
  }
}

async function saveLayout() {
  if (!SCEN_PATH || !MOVED.size) { $("layout-msg").textContent = "nothing moved."; return; }
  const coords = {};
  for (const id of MOVED) {
    const n = TOPO.nodes.find((n) => n.id === id);
    if (n) coords[id] = [n.x, n.y];
  }
  const r = await fetch("api/scenario/coordinates", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: SCEN_PATH, coords }),
  });
  const res = await r.json();
  const m = $("layout-msg");
  if (res.ok) { m.textContent = `saved ${res.saved} node(s); model still solves ✓`; m.className = "msg dim"; MOVED.clear(); }
  else { m.textContent = `rejected (model would not solve): ${res.error}`; m.className = "msg err"; }
}

// ------------------------------------------------------------ attack builder
function addAttack() {
  const spec = {
    id: `a${ATTACKS.length + 1}`,
    kind: $("atk-kind").value,
    target: $("atk-plc").value.trim(),
    start_h: +$("atk-start").value, end_h: +$("atk-end").value,
  };
  if (spec.kind !== "dos") { spec.tag = $("atk-tag").value.trim(); spec.mode = $("atk-mode").value; spec.value = +$("atk-value").value; }
  const path = SCEN_PATH;
  fetch("api/attacks/validate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, attacks: [spec] }),
  }).then((r) => r.json()).then((v) => {
    const res = v.results[0];
    const m = $("atk-msg");
    if (!res.ok) { m.textContent = `invalid: ${res.error}`; m.className = "msg err"; return; }
    ATTACKS.push(spec);
    renderAttacks();
    m.textContent = `added ${spec.id} (victim ${res.resolved.victim_ip})`; m.className = "msg dim";
  });
}

function renderAttacks() {
  const ul = $("atk-list");
  ul.innerHTML = "";
  ATTACKS.forEach((a, i) => {
    const li = document.createElement("li");
    const detail = a.kind === "dos" ? "" : ` ${a.tag} ${a.mode}=${a.value}`;
    li.innerHTML = `<span>${a.kind} ${a.target}${detail} [${a.start_h}–${a.end_h}h]</span>`;
    const x = document.createElement("a");
    x.textContent = " ✕"; x.href = "#"; x.style.color = "var(--crit)";
    x.onclick = (e) => { e.preventDefault(); ATTACKS.splice(i, 1); renderAttacks(); };
    li.appendChild(x);
    ul.appendChild(li);
  });
  const has = ATTACKS.length > 0;
  $("btn-atk-export").disabled = !has;
  $("btn-atk-run").disabled = !has;
}

function attackDeployment() {
  return {
    profile: "full", seed: +$("p-seed").value, start: "2025-01-23",
    hours: +$("p-hours").value, step: +$("p-step").value,
    tick: +$("p-tick").value, period: String($("p-tick").value), capture: true,
  };
}

async function runAttack() {
  ATKSERIES = { h: [], tru: [], seen: [], act: [] };
  $("atk-chart").innerHTML = "";
  const name = `gui_${Date.now().toString().slice(-6)}`;
  const experiment = { name, deployment: attackDeployment(), attacks: ATTACKS };
  const r = await fetch("api/attacks/run", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ experiment }),
  });
  const s = await r.json();
  const m = $("atk-msg");
  if (s.error) { m.textContent = s.error; m.className = "msg err"; return; }
  m.textContent = `running ${name} on the testbed…`; m.className = "msg dim";
}

let ATTACK_WS = null;
function connectAttackWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ATTACK_WS = new WebSocket(`${proto}://${location.host}/ws/attack`);
  ATTACK_WS.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "attack_frame") renderAttackLive(msg);
    else if (msg.type === "attack_status") onAttackStatus(msg);
  };
  ATTACK_WS.onclose = () => setTimeout(connectAttackWS, 1500);
}

function renderAttackLive(f) {
  $("atk-progress-wrap").style.display = "block";
  $("atk-chart").style.display = "block";
  if (f.progress != null) $("atk-progress").style.width = (f.progress * 100).toFixed(1) + "%";
  const ph = $("atk-phase");
  ph.textContent = f.attack_active ? `⚠ ATTACK ACTIVE — ${(f.active_ids || []).join(", ")}` : (f.phase || "running");
  ph.className = "phase" + (f.attack_active ? " active" : "");
  // accumulate series (skip duplicate sim_h)
  const n = ATKSERIES.h.length;
  if (f.sim_h != null && (n === 0 || f.sim_h !== ATKSERIES.h[n - 1])) {
    ATKSERIES.h.push(f.sim_h); ATKSERIES.tru.push(f.true); ATKSERIES.seen.push(f.mtu_seen); ATKSERIES.act.push(f.attack_active);
    drawAtkChart();
  }
  const diff = (f.true != null && f.mtu_seen != null) ? Math.abs(f.true - f.mtu_seen) : null;
  $("atk-live").innerHTML = `
    <table>
      <tr><td>sim time</td><td>${f.sim_h != null ? f.sim_h.toFixed(2) + " h" : "—"}</td></tr>
      <tr><td>true reading</td><td style="color:#60a5fa">${f.true != null ? f.true.toFixed(2) : "—"}</td></tr>
      <tr><td>MTU sees</td><td style="color:${diff > 0.05 ? "var(--crit)" : "var(--ink)"}">${f.mtu_seen != null ? f.mtu_seen.toFixed(2) : "—"}</td></tr>
    </table>`;
}

function onAttackStatus(s) {
  const run = s.state === "running";
  $("btn-atk-run").disabled = run || !ATTACKS.length;
  $("btn-atk-stop").disabled = !run;
  $("btn-atk-dataset").disabled = s.state !== "done";
  if (run && s.progress === 0) { ATKSERIES = { h: [], tru: [], seen: [], act: [] }; }  // new run
  const m = $("atk-msg");
  if (s.state === "running") { $("atk-phase").textContent = s.phase || "running"; }
  else if (s.state === "done") {
    $("atk-progress").style.width = "100%";
    $("atk-phase").textContent = "finished ✓"; $("atk-phase").className = "phase";
    let extra = "";
    if (s.metrics) {
      const mk = Object.entries(s.metrics).filter(([k]) => k.endsWith("_events") && s.metrics[k] > 0);
      extra = mk.length ? " — critical events: " + mk.map(([k, v]) => `${k.replace("_events", "")}:${v}`).join(", ") : "";
    }
    m.textContent = "run finished ✓ — Export dataset, or reproduce from Saved runs." + extra; m.className = "msg dim";
    loadResults();
  } else if (s.state === "error") {
    $("atk-phase").textContent = "failed"; $("atk-phase").className = "phase active";
    m.textContent = "run failed: " + (s.error || ""); m.className = "msg err";
  }
}

async function exportDataset() {
  $("atk-msg").textContent = "building dataset…";
  const r = await fetch("api/attacks/dataset", { method: "POST" });
  const d = await r.json();
  if (d.error) { $("atk-msg").textContent = d.error; $("atk-msg").className = "msg err"; return; }
  window.location = `api/attacks/dataset/download?name=${encodeURIComponent(d.name)}`;
  $("atk-msg").textContent = `dataset ${d.name}: ${d.rows} rows, labels ${JSON.stringify(d.labels)}`;
}

async function loadResults() {
  const r = await (await fetch("api/attacks/results")).json();
  const ul = $("results-list");
  ul.innerHTML = "";
  if (!r.results.length) { ul.innerHTML = '<li class="dim">No runs yet.</li>'; return; }
  for (const run of r.results) {
    const li = document.createElement("li");
    const atk = run.attacks.map((a) => a[0] + "→" + a[1]).join(", ");
    li.innerHTML = `<span>${run.name} <span class="dim">(${atk})${run.finished ? " ✓" : ""}</span></span>`;
    const rep = document.createElement("a");
    rep.textContent = " ↻ reproduce"; rep.href = "#"; rep.style.color = "var(--accent2)";
    rep.onclick = (e) => { e.preventDefault(); reproduce(run.name); };
    li.appendChild(rep);
    ul.appendChild(li);
  }
}

async function reproduce(name) {
  const r = await fetch("api/attacks/reproduce", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const s = await r.json();
  $("atk-msg").textContent = s.error ? s.error : `reproducing ${name} from saved files…`;
  $("atk-msg").className = s.error ? "msg err" : "msg dim";
}

async function exportExperiment() {
  const name = `custom_${Date.now().toString().slice(-6)}`;
  const r = await fetch("api/attacks/experiment", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, attacks: ATTACKS }),
  });
  const text = await r.text();
  const blob = new Blob([text], { type: "text/yaml" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `${name}.yaml`; a.click();
  URL.revokeObjectURL(a.href);
  $("atk-msg").textContent = `exported ${name}.yaml — run: python -m scadavt.attacks.runner ${name}.yaml`;
}

init();
