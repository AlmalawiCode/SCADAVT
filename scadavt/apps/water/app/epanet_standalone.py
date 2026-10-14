"""Run EPANET entirely on its own — no CORE, no PLCs, no SCADAVT.

This demonstrates that EPANET is an autonomous hydraulic simulator: given the
network, demands, patterns, pumps and valves in the ``.inp``, it simulates the
full extended period by itself. SCADAVT is NOT involved here. The resulting
tank/level trace is the physical baseline to compare against a SCADA-controlled
run or an attacked run (the difference is the effect of supervisory control, or
of the attack).

    python -m scadavt.examples.epanet_standalone --config lab.json --cycles 60 --step 120

No external command is ever applied: actuators keep whatever the ``.inp`` sets
(and whatever EPANET's own [CONTROLS]/[RULES] decide, if any). That is exactly
the point — this is EPANET running without the SCADA layer.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from scadavt.protocols import wdsproto as wp


def baseline(design: dict, cycles: int = 60, step_seconds: float = 120.0) -> list:
    """Advance EPANET with NO external control; return the per-cycle tank levels."""
    from scadavt import scenario as sc
    from scadavt.builder import generate
    from scadavt.wds.model import Plant
    tmp = tempfile.mkdtemp(prefix="scadavt_baseline_")
    scada = generate.write_scenario(design, tmp)
    plant = Plant(sc.load(scada), seed=1)
    tanks = plant.net.tanks
    trace = []
    for _ in range(cycles):
        plant.advance(step_seconds)          # EPANET advances by its OWN hydraulic step
        trace.append({t: round(plant.net.get(t, wp.PT_NODE, wp.DT_LEVEL), 3) for t in tanks})
    plant.close()
    return trace


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="lab config JSON (uses its epanet design)")
    ap.add_argument("--cycles", type=int, default=60)
    ap.add_argument("--step", type=float, default=120.0)
    a = ap.parse_args(argv)
    design = json.loads(Path(a.config).read_text())["epanet"]
    trace = baseline(design, a.cycles, a.step)
    tanks = list(trace[0]) if trace else []
    print(f"EPANET standalone (no SCADA): {len(trace)} cycles, tanks {tanks}")
    for t in tanks:
        series = [r[t] for r in trace]
        print(f"  {t}: min={min(series):.2f} max={max(series):.2f} "
              f"start={series[0]:.2f} end={series[-1]:.2f}")


if __name__ == "__main__":
    main()
