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

async function init() {
  const r = await fetch("api/scenario");
  TOPO = await r.json();
  if (TOPO.error) { $("run-msg").textContent = TOPO.error; return; }
  $("scenario-name").value = TOPO.scenario;
  const maxv = Object.values(TOPO.tank_max_level || {});
  if (maxv.length) MAXL = Math.max(...maxv);
  CRIT = 0.15 * MAXL;
  drawNetwork();
  await refreshStatus();
  connectWS();
  $("btn-start").onclick = startRun;
  $("btn-stop").onclick = stopRun;
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
  REF = {};

  // links first (drawn under nodes)
  for (const lk of TOPO.links) {
    const a = XY[lk.n1], b = XY[lk.n2];
    if (!a || !b) continue;
    const stroke = lk.kind === "pump" ? "#3b82f6" : lk.kind === "valve" ? "#f59e0b" : "#3a4656";
    const w = lk.kind === "pipe" ? R * 0.35 : R * 0.6;
    net.appendChild(el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke, "stroke-width": w, "stroke-linecap": "round" }));
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
      const s = R * 2.4;
      const box = el("rect", { x: p.x - s, y: p.y - s, width: 2 * s, height: 2 * s, rx: R * 0.4,
        fill: "#101720", stroke: "#3a4656", "stroke-width": R * 0.25 });
      const fill = el("rect", { x: p.x - s, y: p.y, width: 2 * s, height: 0, fill: "#2f6fb0", rx: R * 0.3 });
      box.style.cursor = fill.style.cursor = "pointer";
      const sel = () => select(n.id);
      box.addEventListener("click", sel); fill.addEventListener("click", sel);
      net.appendChild(box); net.appendChild(fill);
      net.appendChild(label(n.id, p.x, p.y - s - R * 0.6));
      REF[n.id] = { level: fill, tankTop: p.y - s, tankH: 2 * s };
    } else if (n.kind === "reservoir") {
      net.appendChild(el("circle", { cx: p.x, cy: p.y, r: R * 1.6, fill: "#1f6f5c", stroke: "#0d1219", "stroke-width": R * 0.2 }));
      net.appendChild(label(n.id, p.x, p.y - R * 2.2));
    } else {
      net.appendChild(el("circle", { cx: p.x, cy: p.y, r: R * 0.45, fill: "#64748b" }));
    }
  }
}

function label(txt, x, y) {
  const t = el("text", { x, y, "text-anchor": "middle", class: "nlabel" });
  t.textContent = txt;
  return t;
}

function applyFrame(row) {
  if (!row) return;
  // tanks
  for (const id in REF) {
    const ref = REF[id];
    if (ref.level) {
      const lvl = row[`${id}_level`];
      if (lvl != null) {
        const frac = Math.max(0, Math.min(1, lvl / MAXL));
        ref.level.setAttribute("y", ref.tankTop + ref.tankH * (1 - frac));
        ref.level.setAttribute("height", ref.tankH * frac);
        ref.level.setAttribute("fill", lvl <= CRIT ? "#b03a3a" : "#2f6fb0");
      }
    }
    if (ref.pump) {
      const st = row[`${id}_status`];
      if (st != null) ref.pump.setAttribute("fill", st >= 0.5 ? "#22c55e" : "#5b6675");
    }
    if (ref.valve) {
      const fl = row[`${id}_flow`];
      if (fl != null) ref.valve.setAttribute("fill", Math.abs(fl) > 1 ? "#f59e0b" : "#8a6a28");
    }
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

init();
