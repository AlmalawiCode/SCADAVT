"""Put networked runs next to the matching process-level runs.

    python -m scadavt.analysis.compare_networked --results results

For every ``results/networked/<name>/summary.json`` the script finds the
process-level run with the same seed and the same kind of fault, and writes a
table of the measures of the paper (hours below critical, unmet demand, pump
energy) for both modes to ``results/networked/comparison.csv``. The difference
between the two modes is the effect of the real network: polling delays, lost
connections and the timing of commands.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

MEASURES = ["T2_hours_below", "T3_hours_below", "B_unmet_m3", "C_unmet_m3", "energy_kwh"]


def process_match(summary: pd.DataFrame, run: dict) -> pd.Series | None:
    seed = int(run["deployment"].get("seed", 1))
    targets = {f.get("target") for f in run.get("faults", [])}
    if not targets:
        rows = summary[(summary.exp == "E1") & summary.control & (summary.seed == seed)]
    elif targets == {"plc6"}:
        rows = summary[(summary.exp == "E3") & (summary.fault == "hold:PLC6") &
                       (summary.seed == seed)]
    elif targets == {"plc4"}:
        rows = summary[(summary.exp == "E3") & (summary.fault == "off:PLC4") &
                       (summary.seed == seed)]
    else:
        return None
    return rows.iloc[0] if len(rows) else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results"))
    a = ap.parse_args(argv)
    summary = pd.read_csv(a.results / "summary.csv")
    out = []
    for f in sorted((a.results / "networked").glob("*/summary.json")):
        run = json.loads(f.read_text())
        if "metrics" not in run or run["deployment"].get("profile") == "validate":
            continue  # placeholder deployments carry no SCADA programs
        row = {"name": run["name"]}
        ref = process_match(summary, run)
        for m in MEASURES:
            row[f"networked_{m}"] = run["metrics"][m]
            row[f"process_{m}"] = None if ref is None else ref[m]
        out.append(row)
    df = pd.DataFrame(out)
    path = a.results / "networked" / "comparison.csv"
    df.to_csv(path, index=False)
    print(df.to_string(index=False) if len(df) else "no networked runs with metrics yet")


if __name__ == "__main__":
    main()
