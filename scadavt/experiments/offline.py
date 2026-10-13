"""Offline reference run: plant and supervisor in one process, no network.

The supervisor reads the tags directly from the hydraulic engine and its
commands are applied directly. This is the ideal control loop against which
the networked testbed (with real Modbus/TCP traffic, delays and attacks) can
be compared, and it is fast enough for tuning the scenario.

    python -m scadavt.experiments.offline --hours 168 --control on --out run.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

from .. import scenario as sc
from ..master.control import Supervisor
from ..wds.model import Plant


def run(hours: float = 168, control: bool = True, period: int = 300, seed: int = 1,
        start: dt.date | None = None, series: Path | None = None,
        scenario_path=sc.DEFAULT_SCENARIO, faults=()) -> pd.DataFrame:
    """Run the plant under supervision for ``hours`` simulated hours.

    ``faults`` is a list of ``(start_h, end_h, kind, plc)`` windows that model
    the physical consequence of a fault in the supervision loop:
    ``"hold"`` - commands to ``plc`` are not delivered, so the device keeps its
    last setting (loss of the control channel);
    ``"off"``  - status commands to ``plc`` arrive as 0 (pumps held off).
    """
    scen = sc.load(scenario_path)
    plant = Plant(scen, start=start, seed=seed, series_path=series)
    sup = Supervisor(scen.control, scen.doc["tank_max_level"], v1_control=control)
    tags = {(t.plc, t.name): t for t in scen.all_tags()}
    rows = []
    try:
        while plant.net.t < hours * 3600:
            values = {k: plant.net.get(tags[k].element, tags[k].ptype, tags[k].dtype)
                      for k in sup.required_reads()}
            now_h = plant.net.t / 3600.0
            active = [(k, p) for a, b, k, p in faults if a <= now_h < b]
            for cmd in sup.decide(values):
                t = tags[(cmd.plc, cmd.tag)]
                value = cmd.value
                if ("hold", cmd.plc) in active:
                    continue
                if ("off", cmd.plc) in active and t.is_bool:
                    value = 0.0
                plant.net.set(t.element, t.ptype, t.dtype, value)
            plant.advance(period)
            snap = plant.net.snapshot()
            snap["fault"] = int(bool(active))
            rows.append(snap)
    finally:
        plant.close()
    df = pd.DataFrame(rows)
    df["sim_h"] = df["sim_s"] / 3600.0
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=168)
    ap.add_argument("--control", choices=["on", "off"], default="on")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--series", type=Path)
    ap.add_argument("--start", type=dt.date.fromisoformat)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    df = run(a.hours, a.control == "on", seed=a.seed, series=a.series, start=a.start)
    df.to_csv(a.out, index=False)
    print(f"wrote {len(df)} rows to {a.out}")


if __name__ == "__main__":
    main()
