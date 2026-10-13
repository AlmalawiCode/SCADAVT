"""Build a self-contained interactive viewer of the experiment results.

    python -m scadavt.analysis.viewer --results results --out docs/viewer.html

The page animates the water network of the case study (tank levels, pump
states, the flow through valve V1, unmet demand per area) for four runs of the
paper: V1 fixed, V1 supervised, control channel to PLC6 lost, and pumps P4/P5
held off. It needs no server and no internet connection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from ..experiments.suite import CRITICAL, WINDOWS

RUNS = {
    "fixed": ("V1 fixed (no supervision)", "ts_E1_noctrl.csv", []),
    "supervised": ("V1 supervised (flow balancing)", "ts_E1_ctrl.csv", []),
    "plc6": ("Supervised, control channel to PLC6 lost", "ts_E3_hold_PLC6.csv", WINDOWS),
    "plc4": ("Supervised, pumps P4/P5 held off at PLC4", "ts_E3_off_PLC4.csv", WINDOWS),
}
TANKS = ["T1", "T2", "T3", "T4", "T5", "T6"]
PUMPS = [f"P{i}" for i in range(1, 13)]
AREAS = "ABCDEF"


def pack(df: pd.DataFrame, every: int = 3) -> dict:
    d = df.iloc[::every].reset_index(drop=True)
    out = {"h": d["sim_h"].round(3).tolist()}
    for t in TANKS:
        out[t] = d[f"{t}_level"].round(3).tolist()
    for p in PUMPS:
        out[p] = d[f"{p}_status"].astype(int).tolist()
    out["V1"] = d["V1_flow"].round(0).tolist()
    for a in AREAS:
        dem = [c for c in d if c.startswith(a) and c.endswith("_demand")]
        dfc = [c for c in d if c.startswith(a) and c.endswith("_deficit")]
        out[f"dem_{a}"] = d[dem].sum(axis=1).round(0).tolist()
        out[f"def_{a}"] = d[dfc].sum(axis=1).round(1).tolist()
    return out


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCADAVT Viewer</title>
<style>
:root{--blue:#3498DB;--red:#E74C3C;--green:#2ECC71;--orange:#F39C12;--purple:#9B59B6;
--ink:#2C3E50;--muted:#7F8C8D;--line:#D5DBDB;--bg:#FFFFFF;--panel:#FFFFFF;}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Georgia,"Times New Roman",serif}
header{padding:18px 16px 6px;max-width:1180px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
p.sub{margin:0;color:var(--muted)}
main{max-width:1180px;margin:0 auto;padding:8px 16px 32px;display:grid;gap:14px;grid-template-columns:1fr}
.card{border:1px solid var(--line);border-radius:8px;padding:12px;background:var(--panel)}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
select,button{font:inherit;padding:6px 10px;border:1px solid var(--ink);border-radius:6px;background:#fff;color:var(--ink);cursor:pointer}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff}
input[type=range]{flex:1;min-width:200px;accent-color:var(--blue)}
.clock{font-weight:bold;min-width:150px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.kpi{border:1px solid var(--line);border-radius:8px;padding:10px}
.kpi .v{font-size:22px;font-weight:bold}
.kpi .l{color:var(--muted);font-size:12.5px}
.grid2{display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:960px){.grid2{grid-template-columns:1.35fr 1fr}}
svg{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12.5px;color:var(--ink);margin-top:6px}
.sw{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:4px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;border:1px solid var(--red);color:var(--red)}
footer{max-width:1180px;margin:0 auto;padding:0 16px 24px;color:var(--muted);font-size:12.5px}
</style></head><body>
<header>
<h1>SCADAVT viewer: small-town water distribution system</h1>
<p class="sub">Process-level runs of the paper (summer design week starting 23 January 2025, demand seed 1). Choose a run and press play.</p>
</header>
<main>
<section class="card controls" aria-label="Playback controls">
  <select id="run" aria-label="Run"></select>
  <button id="play" class="primary">Play</button>
  <select id="speed" aria-label="Speed"><option value="1">1×</option><option value="3" selected>3×</option><option value="8">8×</option></select>
  <input id="t" type="range" min="0" value="0" aria-label="Simulated time">
  <span class="clock" id="clock"></span>
  <span id="fault" class="badge" hidden>fault active</span>
</section>
<section class="kpis" aria-label="Indicators">
  <div class="kpi"><div class="v" id="k1">0.0 h</div><div class="l">T2 below critical level, so far</div></div>
  <div class="kpi"><div class="v" id="k2">0.0 h</div><div class="l">T3 below critical level, so far</div></div>
  <div class="kpi"><div class="v" id="k3">0.0 m³</div><div class="l">Unmet demand, areas B and C, so far</div></div>
  <div class="kpi"><div class="v" id="k4">0 L/min</div><div class="l">Flow through valve V1 now</div></div>
</section>
<div class="grid2">
<section class="card"><svg id="net" viewBox="0 0 1000 560" role="img" aria-label="Water network"></svg>
<div class="legend"><span><span class="sw" style="background:var(--blue);height:10px"></span>water level</span><span><span class="sw" style="background:var(--red)"></span>critical level (15 %)</span><span><span class="sw" style="background:var(--green);height:10px;border-radius:5px"></span>pump on</span><span><span class="sw" style="background:var(--muted);height:10px;border-radius:5px"></span>pump off</span><span><span class="sw" style="background:var(--red);height:10px"></span>area with unmet demand</span></div></section>
<section class="card"><svg id="chart" viewBox="0 0 520 330" role="img" aria-label="Levels of T2 and T3"></svg>
<div class="legend"><span><span class="sw" style="background:var(--blue)"></span>Tank T2</span><span><span class="sw" style="background:var(--purple)"></span>Tank T3</span><span><span class="sw" style="background:var(--red)"></span>critical level</span><span><span class="sw" style="border-top:2px dashed var(--red);height:0"></span>fault window</span></div></section>
</div>
</main>
<footer>Generated by <code>scadavt.analysis.viewer</code> from the result files of the experiment suite. Values every 15 simulated minutes.</footer>
<script>
const DATA = __DATA__;
const CRIT = __CRIT__, MAXL = 7.0;
const C = {blue:"#3498DB",red:"#E74C3C",green:"#2ECC71",orange:"#F39C12",purple:"#9B59B6",ink:"#2C3E50",muted:"#7F8C8D"};
const NS="http://www.w3.org/2000/svg";
const el=(tag,attrs,parent)=>{const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(parent)parent.appendChild(e);return e;};
const txt=(x,y,s,parent,extra={})=>{const t=el("text",Object.assign({x,y,"font-size":13,"font-family":"Georgia,serif",fill:C.ink,"text-anchor":"middle"},extra),parent);t.textContent=s;return t;};

const POS={T1:[180,330],T2:[180,170],T3:[420,175],T4:[800,330],T5:[800,170],T6:[600,190]};
const AREA={A:[330,470,"T1"],B:[60,60,"T2"],C:[420,40,"T3"],D:[660,470,"T4"],E:[930,60,"T5"],F:[600,60,"T6"]};
const PUMP={P1:[80,470],P2:[105,470],P3:[130,470],P4:[160,238],P5:[200,238],P6:[690,300],P7:[715,300],P8:[880,470],P9:[905,470],P10:[780,238],P11:[820,238],P12:[510,175]};
const net=document.getElementById("net");
function pipe(pts,w=4){el("polyline",{points:pts.map(p=>p.join(",")).join(" "),fill:"none",stroke:"#5D6D7E","stroke-width":w,"stroke-linejoin":"round"},net);}
pipe([[40,470],[180,470],[180,360]]);pipe([[180,300],[180,200]]);pipe([[210,170],[390,170]]);
pipe([[960,470],[800,470],[800,360]]);pipe([[800,300],[800,200]]);pipe([[960,470],[960,400],[600,400],[600,220]]);pipe([[570,190],[450,170]]);
for(const a in AREA){const [x,y,t]=AREA[a];const [tx,ty]=POS[t];pipe([[tx,ty],[x,y]],3);}
const W={};
el("rect",{x:20,y:448,width:46,height:44,rx:6,fill:"#fff",stroke:C.ink,"stroke-width":2},net);txt(43,512,"Works 1",net,{"font-size":12});
el("rect",{x:934,y:448,width:46,height:44,rx:6,fill:"#fff",stroke:C.ink,"stroke-width":2},net);txt(957,512,"Works 2",net,{"font-size":12});
for(const t in POS){const [x,y]=POS[t];const g=el("g",{},net);
  el("rect",{x:x-30,y:y-40,width:60,height:80,rx:4,fill:"#fff",stroke:C.ink,"stroke-width":2},g);
  W[t]=el("rect",{x:x-28,y:y+38,width:56,height:0,fill:C.blue},g);
  const cy=y+38-76*CRIT/MAXL; el("line",{x1:x-30,x2:x+30,y1:cy,y2:cy,stroke:C.red,"stroke-width":2,"stroke-dasharray":"5 3"},g);
  txt(x,y-48,t,g,{"font-weight":"bold"}); W[t+"_lbl"]=txt(x,y+60,"",g,{"font-size":12});}
const valve=el("g",{},net);const vx=300,vy=170;
el("polygon",{points:`${vx-14},${vy-12} ${vx-14},${vy+12} ${vx},${vy}`,fill:C.red,stroke:C.ink},valve);
el("polygon",{points:`${vx+14},${vy-12} ${vx+14},${vy+12} ${vx},${vy}`,fill:C.red,stroke:C.ink},valve);
txt(vx,vy-20,"V1",valve,{"font-weight":"bold",fill:C.red});W.V1=txt(vx,vy+30,"",valve,{"font-size":12});
for(const p in PUMP){const [x,y]=PUMP[p];W[p]=el("circle",{cx:x,cy:y,r:10,fill:C.muted,stroke:C.ink,"stroke-width":1.5},net);txt(x,y+24,p,net,{"font-size":10});}
for(const a in AREA){const [x,y]=AREA[a];const g=el("g",{},net);
  W["A"+a]=el("polygon",{points:`${x-22},${y+4} ${x},${y-18} ${x+22},${y+4}`,fill:C.orange,stroke:C.ink},g);
  el("rect",{x:x-16,y:y+4,width:32,height:20,fill:"#fff",stroke:C.ink},g);
  txt(x,y+40,"Area "+a,g,{"font-weight":"bold","font-size":12});W["D"+a]=txt(x,y+55,"",g,{"font-size":11});}

const chart=document.getElementById("chart");const CX0=48,CX1=505,CY0=20,CY1=290;
const sx=h=>CX0+(CX1-CX0)*h/168, sy=v=>CY1-(CY1-CY0)*v/7.5;
let cursor, faultG;
function drawChart(run){chart.innerHTML="";const d=DATA[run];
  for(let v=0;v<=7;v++){el("line",{x1:CX0,x2:CX1,y1:sy(v),y2:sy(v),stroke:"#E5E8E8","stroke-dasharray":"3 3"},chart);txt(CX0-8,sy(v)+4,v,chart,{"text-anchor":"end","font-size":11});}
  for(let h=0;h<=168;h+=24){txt(sx(h),CY1+18,h,chart,{"font-size":11});}
  txt((CX0+CX1)/2,CY1+36,"Simulated time (hours)",chart,{"font-size":12});
  const yl=txt(14,(CY0+CY1)/2,"Water level (m)",chart,{"font-size":12});yl.setAttribute("transform",`rotate(-90 14 ${(CY0+CY1)/2})`);
  for(const [a,b] of d.windows){el("rect",{x:sx(a),y:CY0,width:sx(b)-sx(a),height:CY1-CY0,fill:"none",stroke:C.red,"stroke-dasharray":"4 3"},chart);}
  el("line",{x1:CX0,x2:CX1,y1:sy(CRIT),y2:sy(CRIT),stroke:C.red,"stroke-width":1.5,"stroke-dasharray":"6 3"},chart);
  for(const [t,c] of [["T3",C.purple],["T2",C.blue]]){el("polyline",{points:d.h.map((h,i)=>sx(h)+","+sy(d[t][i])).join(" "),fill:"none",stroke:c,"stroke-width":1.8},chart);}
  el("rect",{x:CX0,y:CY0,width:CX1-CX0,height:CY1-CY0,fill:"none",stroke:C.ink},chart);
  cursor=el("line",{x1:CX0,x2:CX0,y1:CY0,y2:CY1,stroke:C.ink,"stroke-width":1.5},chart);}

const runSel=document.getElementById("run"),slider=document.getElementById("t"),clock=document.getElementById("clock");
for(const k in DATA){const o=document.createElement("option");o.value=k;o.textContent=DATA[k].label;runSel.appendChild(o);}
let run=Object.keys(DATA)[0], i=0, timer=null, acc=null;
function cumulate(){const d=DATA[run],n=d.h.length,dt=d.h[1]-d.h[0];acc={b2:[0],b3:[0],u:[0]};
  for(let j=1;j<n;j++){acc.b2.push(acc.b2[j-1]+(d.T2[j]<CRIT?dt:0));acc.b3.push(acc.b3[j-1]+(d.T3[j]<CRIT?dt:0));
    acc.u.push(acc.u[j-1]+(d.def_B[j]+d.def_C[j])*dt*60/1000);}}
function show(j){i=j;slider.value=j;const d=DATA[run];const h=d.h[j];
  const day=Math.floor(h/24),hh=Math.floor(h%24),mm=Math.round((h%1)*60)%60;
  clock.textContent=`Day ${day+1}, ${String(hh).padStart(2,"0")}:${String(mm).padStart(2,"0")} (hour ${h.toFixed(1)})`;
  for(const t in POS){const lv=d[t][j],hgt=76*lv/MAXL,[x,y]=POS[t];W[t].setAttribute("y",y+38-hgt);W[t].setAttribute("height",hgt);
    W[t].setAttribute("fill",lv<CRIT?C.red:C.blue);W[t+"_lbl"].textContent=lv.toFixed(2)+" m";}
  for(const p in PUMP){W[p].setAttribute("fill",d[p][j]?C.green:C.muted);}
  W.V1.textContent=Math.round(d.V1[j])+" L/min";
  for(const a in AREA){const df=d["def_"+a][j];W["A"+a].setAttribute("fill",df>1?C.red:C.orange);
    W["D"+a].textContent=df>1?`unmet ${Math.round(df)} L/min`:`${Math.round(d["dem_"+a][j])} L/min`;}
  const inFault=d.windows.some(([a,b])=>h>=a&&h<b);document.getElementById("fault").hidden=!inFault;
  document.getElementById("k1").textContent=acc.b2[j].toFixed(1)+" h";document.getElementById("k2").textContent=acc.b3[j].toFixed(1)+" h";
  document.getElementById("k3").textContent=acc.u[j].toFixed(1)+" m³";document.getElementById("k4").textContent=Math.round(d.V1[j])+" L/min";
  cursor.setAttribute("x1",sx(h));cursor.setAttribute("x2",sx(h));}
function select(r){run=r;cumulate();slider.max=DATA[r].h.length-1;drawChart(r);show(Math.min(i,DATA[r].h.length-1));}
runSel.onchange=()=>select(runSel.value);slider.oninput=()=>show(+slider.value);
document.getElementById("play").onclick=function(){if(timer){clearInterval(timer);timer=null;this.textContent="Play";return;}
  this.textContent="Pause";timer=setInterval(()=>{const s=+document.getElementById("speed").value;let j=i+s;if(j>=DATA[run].h.length){j=0;}show(j);},60);};
select(run);
</script></body></html>
"""


def build(results: Path, out: Path) -> Path:
    data = {}
    for key, (label, fname, windows) in RUNS.items():
        df = pd.read_csv(results / fname)
        d = pack(df)
        d["label"] = label
        d["windows"] = [list(w) for w in windows]
        data[key] = d
    html = HTML.replace("__DATA__", json.dumps(data, separators=(",", ":"))).replace(
        "__CRIT__", str(CRITICAL))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--out", type=Path, default=Path("docs/viewer.html"))
    a = ap.parse_args(argv)
    print(build(a.results, a.out))


if __name__ == "__main__":
    main()
