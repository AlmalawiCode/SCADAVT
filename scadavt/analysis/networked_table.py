"""Table of the closed-loop runs on the emulated network (experiment E6).

    python -m scadavt.analysis.networked_table --results results --out ../paper/tables

For every week-long networked run it reports the share of successful MTU
connections (outside the start-up cycles), the outcome of the MTU's sessions
with PLC6 inside the fault windows, whether the plant's V1 setting still
followed the MTU inside every window (it is frozen when the commands are lost), the plant measures, and the pump energy of the matching process-level
run. It writes ``networked.csv`` next to the results and ``tab_networked.tex``
for the paper.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .compare_networked import process_match

RUNS = [("e1_supervised", "No fault"),
        ("e3_plc6_link", "Link lost"),
        ("e3_site_f_congested", "Rate 8 kbit/s"),
        ("e3_plc6_delay150", "Delay 150 ms"),
        ("e3_plc6_delay300", "Delay 300 ms")]
STARTUP = 4  # cycles before the gateway has delivered every sensor value


def windows_mask(df: pd.DataFrame, faults: pd.DataFrame) -> pd.Series:
    m = pd.Series(False, index=df.index)
    st = faults[faults.phase == "start"].wall_time.values
    en = faults[faults.phase == "end"].wall_time.values
    for a, b in zip(st, en):
        m |= (df.wall_time >= a) & (df.wall_time < b)
    return m


def row(results: Path, summary: pd.DataFrame, name: str, label: str) -> dict | None:
    d = results / "networked" / name
    if not (d / "summary.json").exists():
        return None
    s = json.loads((d / "summary.json").read_text())
    c = pd.read_csv(d / "connections.csv")
    gt = pd.read_csv(d / "ground_truth.csv")
    f = pd.read_csv(d / "faults.csv") if (d / "faults.csv").stat().st_size > 60 else None
    c = c[c.cycle > STARTUP]
    out = {"case": label, "run": name, "connections": len(c)}
    if f is not None and len(f):
        w = windows_mask(c, f)
        p6 = c[w & (c.plc == "PLC6")]
        out["ok_outside"] = round(100 * c[~w].ok.mean(), 2)
        out["plc6_ok_in"] = round(100 * p6.ok.mean(), 1)
        ok = p6[p6.ok == 1]
        # the median of a handful of sessions at the window edges says nothing
        out["plc6_session_ms"] = (round((ok.connect_ms + ok.request_ms).median(), 1)
                                  if len(ok) >= 0.05 * len(p6) else None)
        held = []
        for _, (a, b) in enumerate(zip(f[f.phase == "start"].sim_h, f[f.phase == "end"].sim_h)):
            x = gt[(gt.sim_h > a + 1 / 6) & (gt.sim_h < b)].V1_setting.round(1)
            held.append(x.nunique() == 1)
        out["v1_follows"] = "no" if all(held) else "yes"
    else:
        out["ok_outside"] = round(100 * c.ok.mean(), 2)
        out["plc6_ok_in"] = None
        p6 = c[(c.plc == "PLC6") & (c.ok == 1)]
        out["plc6_session_ms"] = round((p6.connect_ms + p6.request_ms).median(), 1)
        out["v1_follows"] = None
    m = s["metrics"]
    for k in ("T2_hours_below", "T3_hours_below", "B_unmet_m3", "C_unmet_m3", "energy_kwh"):
        out[k] = m[k]
    ref = process_match(summary, s)
    out["process_energy_kwh"] = None if ref is None else float(ref["energy_kwh"])
    return out


def fmt(v, nd=1):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "--"
    if isinstance(v, str):
        return v
    return f"{v:,.{nd}f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    summary = pd.read_csv(a.results / "summary.csv")
    rows = [r for n, lbl in RUNS if (r := row(a.results, summary, n, lbl))]
    df = pd.DataFrame(rows)
    df.to_csv(a.results / "networked" / "networked.csv", index=False)
    print(df.to_string(index=False))
    if a.out:
        lines = []
        for r in rows:
            lines.append(" & ".join([
                r["case"], fmt(r["ok_outside"], 2), fmt(r["plc6_ok_in"]),
                fmt(r["plc6_session_ms"]), fmt(r["v1_follows"]),
                fmt(r["T2_hours_below"] + r["T3_hours_below"]),
                fmt(r["B_unmet_m3"] + r["C_unmet_m3"]),
                fmt(r["energy_kwh"], 0), fmt(r["process_energy_kwh"], 0)]) + r" \\")
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "tab_networked.tex").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
