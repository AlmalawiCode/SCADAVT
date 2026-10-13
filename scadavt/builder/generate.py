"""Turn a design the researcher built (components + links + rules) into a
runnable scenario: a valid EPANET ``.inp`` and a ``scada.yaml``.

A design is JSON from the builder GUI:

    {"name": "demo",
     "nodes": [{"id":"R1","kind":"reservoir","x":0,"y":0,"head":50},
               {"id":"J1","kind":"junction","x":1,"y":0,"elev":0,"demand":4},
               {"id":"T1","kind":"tank","x":2,"y":0,"elev":0,"init":2,"min":0.1,"max":5,"diam":12}],
     "links": [{"id":"P1","kind":"pump","n1":"R1","n2":"J1","power":12},
               {"id":"Pi1","kind":"pipe","n1":"J1","n2":"T1","length":120,"diam":300,"rough":120}],
     "rules": [...],
     "plcs":  [{"id":"PLC1","elements":["T1","P1"]}]}

Nothing here solves hydraulics; ``scadavt.wds.model.Plant`` does, and the lab
runs a real EPANET solve to validate a design before it is used.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# quantity name per component kind, for the SCADA tag table
_SENSOR = {"tank": ("level", "node"), "junction": ("pressure", "node"),
           "reservoir": ("head", "node")}


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def generate_inp(design: dict) -> str:
    nodes = {n["id"]: n for n in design.get("nodes", [])}
    links = design.get("links", [])
    J, R, T, PI, PU, VA, CO = [], [], [], [], [], [], []
    for n in design.get("nodes", []):
        k = n["kind"]
        if k == "junction":
            J.append(f" {n['id']:<16}{_f(n.get('elev')):>10.2f}{_f(n.get('demand')):>12.3f}")
        elif k == "reservoir":
            R.append(f" {n['id']:<16}{_f(n.get('head'), 50):>10.2f}")
        elif k == "tank":
            T.append(f" {n['id']:<16}{_f(n.get('elev')):>8.2f}{_f(n.get('init'), 2):>8.2f}"
                     f"{_f(n.get('min'), 0.1):>8.2f}{_f(n.get('max'), 5):>8.2f}"
                     f"{_f(n.get('diam'), 12):>8.2f}{0.0:>8.2f}")
        CO.append(f" {n['id']:<16}{_f(n.get('x')):>16.2f}{_f(n.get('y')):>16.2f}")
    for lk in links:
        k = lk["kind"]
        if k == "pipe":
            PI.append(f" {lk['id']:<16}{lk['n1']:<16}{lk['n2']:<16}{_f(lk.get('length'),100):>10.2f}"
                      f"{_f(lk.get('diam'),300):>10.2f}{_f(lk.get('rough'),120):>10.2f}"
                      f"{0.0:>10.2f}  {lk.get('status','Open')}")
        elif k == "pump":
            PU.append(f" {lk['id']:<16}{lk['n1']:<16}{lk['n2']:<16}  POWER {_f(lk.get('power'),12):g}")
        elif k == "valve":
            VA.append(f" {lk['id']:<16}{lk['n1']:<16}{lk['n2']:<16}{_f(lk.get('diam'),300):>8.2f}"
                      f"  {lk.get('vtype','TCV'):<5}{_f(lk.get('setting')):>10.2f}{0.0:>8.2f}")
    # links joining two nodes that lack a pipe still need coordinates to resolve;
    # EPANET derives link geometry from node coords, so only nodes need COORDINATES.
    hours = _f(design.get("hours"), 168)
    sections = [
        ("[TITLE]", [design.get("name", "scadavt-design")]),
        ("[JUNCTIONS]", [";ID Elev Demand"] + J),
        ("[RESERVOIRS]", [";ID Head"] + R),
        ("[TANKS]", [";ID Elev InitLvl MinLvl MaxLvl Diam MinVol"] + T),
        ("[PIPES]", [";ID N1 N2 Length Diam Rough Minor Status"] + PI),
        ("[PUMPS]", [";ID N1 N2 Params"] + PU),
        ("[VALVES]", [";ID N1 N2 Diam Type Setting Minor"] + VA),
        ("[PATTERNS]", []),
        ("[CURVES]", []),
        ("[ENERGY]", ["Global Efficiency  75"]),
        ("[TIMES]", [f"Duration           {int(hours)}:00",
                     "Hydraulic Timestep 0:05", "Pattern Timestep   1:00",
                     "Report Timestep    0:05", "Start ClockTime    12 am"]),
        ("[REPORT]", ["Status No", "Summary No", "Page 0"]),
        ("[OPTIONS]", ["Units LPS", "Headloss H-W", "Quality None",
                       "Unbalanced Continue 10", "Trials 40", "Accuracy 0.001"]),
        ("[COORDINATES]", [";Node X Y"] + CO),
        ("[END]", []),
    ]
    out = []
    for head, body in sections:
        out.append(head)
        out.extend(body)
        out.append("")
    return "\n".join(out) + "\n"


def generate_scada(design: dict, inp_name: str = "plant.inp") -> dict:
    """Minimal scada.yaml: tags per component assigned to PLCs. Demands come from
    the .inp (population is empty), and control is the rule engine, not pump_groups."""
    nodes = {n["id"]: n for n in design.get("nodes", [])}
    link_kind = {lk["id"]: lk["kind"] for lk in design.get("links", [])}
    plcs_in = design.get("plcs") or [{"id": "PLC1", "elements": list(nodes) + list(link_kind)}]
    # which PLC owns each element
    owner = {}
    for p in plcs_in:
        for e in p.get("elements", []):
            owner.setdefault(e, p["id"])
    plcs: dict[str, dict] = {}
    tank_max = {}
    for p in plcs_in:
        plcs.setdefault(p["id"], {"field": "A", "tags": []})
    for nid, n in nodes.items():
        if n["kind"] == "tank":
            tank_max[nid] = _f(n.get("max"), 5)
        if n["kind"] in _SENSOR:
            q, et = _SENSOR[n["kind"]]
            plc = owner.get(nid, plcs_in[0]["id"])
            plcs[plc]["tags"].append([f"{nid}_{q[:3].upper()}", nid, et, q, "i"])
    for lid, k in link_kind.items():
        plc = owner.get(lid, plcs_in[0]["id"])
        if k == "pump":
            plcs[plc]["tags"].append([f"{lid}_ST_O", lid, "link", "status", "o"])
            plcs[plc]["tags"].append([f"{lid}_FLOW", lid, "link", "flow", "i"])
        elif k == "valve":
            plcs[plc]["tags"].append([f"{lid}_SET_O", lid, "link", "setting", "o"])
            plcs[plc]["tags"].append([f"{lid}_FLOW", lid, "link", "flow", "i"])
    fields = {"A": list(plcs)}
    return {"name": design.get("name", "scadavt-design"), "epanet_file": inp_name,
            "population": {}, "tank_max_level": tank_max, "fields": fields,
            "plcs": plcs, "control": {}, "rules": design.get("rules", [])}


def write_scenario(design: dict, out_dir: str | Path) -> Path:
    """Write plant.inp + scada.yaml into ``out_dir``; return the scada.yaml path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plant.inp").write_text(generate_inp(design))
    scada = generate_scada(design, "plant.inp")
    scada_path = out / "scada.yaml"
    scada_path.write_text(yaml.dump(scada, sort_keys=False))
    return scada_path
