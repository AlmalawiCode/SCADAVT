#!/usr/bin/env python3
"""Live EPANET closed-loop through the Adapter interface on real CORE (M5).

The generic PLC has two registers — ``level`` (input) owned by the process and
``pump`` (coil) owned by the MTU. EPANET is attached only as a ``simulator``
adapter (no water code in the PLC/core):

  EPANET T1.level --(adapter in)--> PLC1.level --(Modbus)--> MTU reads
  MTU logic (level low -> pump on) --(Modbus)--> PLC1.pump --(adapter out)--> EPANET P1
  EPANET advances a hydraulic step -> level responds -> next cycle.

Verifies: measurement mapping (#2), command over Modbus reaching EPANET (#3),
closed-loop response (#4), and MTU ownership of the command register (#5).

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_water_adapter.py

Needs a running core-daemon. Exit 0 only if all checks pass.
"""

from __future__ import annotations

import os
import sys
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.api.grpc import client  # noqa: E402
from scadavt.core import corelive  # noqa: E402

PASS, FAIL = [], []
INP = os.path.join(REPO, "scadavt", "apps", "water", "scenarios", "closed_loop_min", "network.inp")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


CFG = {
    "name": "water_adapter", "control": {"placement": "mtu"},
    "mtu": {"id": "MTU", "ip": "10.0.0.254"},
    "devices": [{"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus", "registers": [
        {"id": "level", "area": "input_register", "data_type": "float32", "owner": "process"},
        {"id": "pump", "area": "coil", "data_type": "bool", "owner": "mtu"}]}],
    "switches": [{"id": "SW"}],
    "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
              {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
    # MTU control: keep the pump on while the tank is below 20 m (fill control)
    "logic": [{"id": "fill", "if": [{"device": "PLC1", "register": "level", "op": "below", "value": 20.0}],
               "then": [{"device": "PLC1", "register": "pump", "set": 1}]}],
    "adapters": [{"id": "ep", "kind": "simulator", "engine": "epanet",
                  "plugin": "scadavt.apps.water.epanet_adapter",
                  "model": {"inp": INP, "step": 300}}],
    "io_map": [
        {"adapter": "ep", "external": {"var": "T1.level"}, "device": "PLC1",
         "register": "level", "direction": "in", "quality": "default", "default": 0.0},
        {"adapter": "ep", "external": {"var": "P1.status"}, "device": "PLC1",
         "register": "pump", "direction": "out"}],
}


def main() -> int:
    print("\n== EPANET closed-loop via the Adapter interface on live CORE ==")
    core = client.CoreGrpcClient(); core.connect()
    net = corelive.build(core, CFG)
    try:
        # start() also launches the adapter host inside the PLC node, which
        # drives level<-EPANET and pump->EPANET (one EPANET step per host cycle,
        # so the period sets how fast simulated time runs)
        net.start(cycles=120, io_period=0.3)
        time.sleep(4.0)
        lvl0 = net.probe("MTU", "PLC1", "level")
        check("#2 EPANET measurement mapped: MTU reads PLC1.level over Modbus", lvl0 is not None
              and lvl0 > 0.0, f"level0={lvl0}")
        time.sleep(6.0)
        pump = net.probe("MTU", "PLC1", "pump")
        check("#5 MTU owns 'pump' and commanded it over Modbus (ownership allows MTU)",
              pump == 1.0, f"pump={pump}")
        time.sleep(6.0)
        lvl1 = net.probe("MTU", "PLC1", "level")
        check("#3/#4 command reached EPANET and the level responded over steps",
              lvl1 is not None and abs(lvl1 - lvl0) > 0.01, f"level {lvl0} -> {lvl1}")
    finally:
        net.stop()

    print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
