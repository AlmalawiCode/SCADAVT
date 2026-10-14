"""Generate a ready-to-run thesis example (water distribution system).

From the dissertation's reconstructed scenario in the repo (``scenarios/town``:
the EPANET network ``town.inp`` + the SCADA description ``scada.yaml`` with its
33 PLCs and documented pump hysteresis), this produces three uploadable files:

  watertown.inp           the EPANET network (the authoritative thesis network)
  consumption_hourly.csv  water consumption per hour (from the real per-person
                          series + the diurnal pattern), over one week
  watertown.json          the lab configuration (EPANET design + PLCs + register
                          bindings + control rules) — upload this and press Run

Everything is derived from the thesis scenario, not invented.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import yaml

from scadavt.labui.config import compose_lab
from scadavt.labui.inp import parse_inp
from scadavt.labui.wds_lab import WdsLab
from scadavt.wds.demand import DemandModel, load_series

PKG = Path(__file__).resolve().parents[1]
TOWN = PKG / "scadavt" / "scenarios" / "town"
OUT = PKG / "scadavt" / "scenarios" / "thesis_example"
CONS = PKG / "data" / "consumption_per_person.csv"


def build_config() -> dict:
    inp_text = (TOWN / "town.inp").read_text()
    scada = yaml.safe_load((TOWN / "scada.yaml").read_text())
    design = parse_inp(inp_text)              # nodes/links + coordinates + inp_text
    tank_max = scada.get("tank_max_level", {})
    groups = scada["control"]["pump_groups"]

    plcs, rules = [], []
    for g in groups:
        T, pumps = g["tank"], g["pumps"]
        mx = float(tank_max.get(T, 7.0))
        on, off = round(g["on_below"] * mx, 2), round(g["off_above"] * mx, 2)
        plcs.append({"id": g["plc"], "elements": [T] + pumps})
        for p in pumps:                        # documented hysteresis, per pump
            rules += [
                {"id": f"on_{p}", "if": [{"kind": "tank", "comp": T, "attr": "level",
                                          "op": "below", "value": on}],
                 "then": [{"comp": p, "kind": "pump", "attr": "status", "value": "open"}]},
                {"id": f"off_{p}", "if": [{"kind": "tank", "comp": T, "attr": "level",
                                           "op": "above", "value": off}],
                 "then": [{"comp": p, "kind": "pump", "attr": "status", "value": "closed"}]},
            ]
    design["name"] = "thesis-water-distribution"
    design["hours"] = 168
    return compose_lab(design, plcs, rules=rules, with_mtu=True,
                       name="thesis-water-distribution")


def write_consumption(hours: int = 168) -> list:
    scada = yaml.safe_load((TOWN / "scada.yaml").read_text())
    population = scada["population"]
    series = load_series(CONS)
    start = series[len(series) // 2][0]
    model = DemandModel(series, population, start, seed=1)
    rows = []
    for h in range(hours):
        d = model.hourly_demands(h * 3600)
        total = sum(d.values())
        rows.append({"hour": h, "day": h // 24, "hour_of_day": h % 24,
                     "total_demand_Lpm": round(total, 1),
                     "total_demand_Lps": round(total / 60.0, 2)})
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # 1) EPANET network
    (OUT / "watertown.inp").write_text((TOWN / "town.inp").read_text())
    # 2) consumption over hours
    rows = write_consumption()
    with open(OUT / "consumption_hourly.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    # 3) lab configuration JSON
    cfg = build_config()
    (OUT / "watertown.json").write_text(json.dumps(cfg, indent=2))

    print(f"wrote:\n  {OUT/'watertown.inp'}\n  {OUT/'consumption_hourly.csv'}"
          f"\n  {OUT/'watertown.json'}")
    print(f"PLCs: {len(cfg['epanet']['plcs'])}  rules: {len(cfg['rules'])}  "
          f"devices: {len(cfg['devices'])}")
    peak = max(r['total_demand_Lps'] for r in rows)
    print(f"consumption: {len(rows)} hours, peak {peak} L/s")

    # verify it runs on real EPANET and the tanks move
    lab = WdsLab(cfg["epanet"], step_seconds=600.0).run(200)
    tr = lab.traces()
    for plc, series in list(tr.items())[:6]:
        print(f"  {plc}: tank {round(min(series),2)}..{round(max(series),2)} m")
    lab.close()


if __name__ == "__main__":
    main()
