"""Experiment suite behind the figures and tables of the paper.

    python -m scadavt.experiments.suite --out results/

E1  supervision on/off: one simulated week, ten demand seeds.
E2  seasons: the same comparison starting in each season of 2025.
E3  fault windows: loss of the control channel to the V1 controller (PLC6)
    and pumps P4/P5 held off (PLC4), in three windows of the week.
E4  window-length sweep: impact of a single fault window of 1-12 hours.

All results are written as CSV files; figures are drawn from these files only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from .offline import run

warnings.filterwarnings("ignore")

STEP_H = 300 / 3600.0
CRITICAL = 0.15 * 7.0  # critical level = 15 % of the 7 m maximum level
WINDOWS = [(15, 20), (55, 57), (100, 105)]
SEASON_STARTS = {"summer": dt.date(2025, 1, 23), "autumn": dt.date(2025, 4, 24),
                 "winter": dt.date(2025, 7, 24), "spring": dt.date(2025, 10, 23)}
START = SEASON_STARTS["summer"]
HOURS = 168


def metrics(df: pd.DataFrame) -> dict:
    m = {}
    for t in ("T1", "T2", "T3", "T4", "T5", "T6"):
        below = df[f"{t}_level"] < CRITICAL
        m[f"{t}_events"] = int((below & ~below.shift(fill_value=False)).sum())
        m[f"{t}_hours_below"] = round(float(below.sum() * STEP_H), 2)
        m[f"{t}_min"] = round(float(df[f"{t}_level"].min()), 3)
    for a in "ABCDEF":
        cols = [c for c in df if c.startswith(a) and c.endswith("_deficit")]
        dem = [c for c in df if c.startswith(a) and c.endswith("_demand")]
        # L/min * 5 min per step -> litres -> m3
        m[f"{a}_unmet_m3"] = round(float(df[cols].sum().sum() * 5 / 1000), 1)
        m[f"{a}_served_m3"] = round(float(df[dem].sum().sum() * 5 / 1000), 1)
    pw = [c for c in df if c.endswith("_power")]
    m["energy_kwh"] = round(float(df[pw].sum().sum() * STEP_H), 1)
    m["v1_mean_lpm"] = round(float(df["V1_flow"].mean()), 1)
    return m


def job(spec: dict):
    t0 = time.perf_counter()
    df = run(spec["hours"], spec["control"], seed=spec["seed"], start=spec["start"],
             faults=spec.get("faults", ()))
    m = metrics(df)
    m.update({k: (v.isoformat() if isinstance(v, dt.date) else v)
              for k, v in spec.items() if k not in ("faults", "keep")})
    m["faults"] = json.dumps(spec.get("faults", []))
    m["runtime_s"] = round(time.perf_counter() - t0, 2)
    return m, (df if spec.get("keep") else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    seeds = range(1, a.seeds + 1)
    jobs = []
    for s in seeds:  # E1
        for c in (False, True):
            jobs.append({"exp": "E1", "control": c, "seed": s, "start": START,
                         "hours": HOURS, "keep": s == 1})
    for season, d in SEASON_STARTS.items():  # E2
        for s in list(seeds)[:5]:
            for c in (False, True):
                jobs.append({"exp": "E2", "season": season, "control": c, "seed": s,
                             "start": d, "hours": HOURS})
    for s in seeds:  # E3
        for kind, plc in (("hold", "PLC6"), ("off", "PLC4")):
            jobs.append({"exp": "E3", "fault": f"{kind}:{plc}", "control": True, "seed": s,
                         "start": START, "hours": HOURS, "keep": s == 1,
                         "faults": [(x, y, kind, plc) for x, y in WINDOWS]})
    for s in list(seeds)[:5]:  # E4
        for L in (1, 2, 3, 4, 6, 8, 10, 12):
            for kind, plc in (("hold", "PLC6"), ("off", "PLC4")):
                jobs.append({"exp": "E4", "fault": f"{kind}:{plc}", "length_h": L,
                             "control": True, "seed": s, "start": START, "hours": HOURS,
                             "faults": [(30, 30 + L, kind, plc)]})
    rows = []
    with ProcessPoolExecutor(a.workers) as ex:
        for m, df in ex.map(job, jobs):
            rows.append(m)
            if df is not None:
                name = f"ts_{m['exp']}_{'ctrl' if m['control'] else 'noctrl'}"
                if m["exp"] == "E3":
                    name = f"ts_E3_{m['fault'].replace(':', '_')}"
                df.to_csv(a.out / f"{name}.csv", index=False)
    pd.DataFrame(rows).to_csv(a.out / "summary.csv", index=False)
    print(f"{len(rows)} runs -> {a.out / 'summary.csv'}")


if __name__ == "__main__":
    main()
