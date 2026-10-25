#!/usr/bin/env python3
"""Live proof of adapter interchangeability (A7) on real CORE/Modbus.

The SAME generic PLC (one input register ``temp``) is driven in turn by three
different adapter kinds — a simulated sensor, a mock hardware driver, and a
process simulator — changing ONLY the external adapter config. Each feeds the
register through the IOModules back channel (``scadavt.core.iohost``), and the
MTU reads the value over real Modbus. All three must yield the same reading.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_adapter.py

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
VALUE = 21.5


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def cfg_for(adapter: dict, var: str) -> dict:
    return {
        "name": "adapter_min", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus", "registers": [
            {"id": "temp", "area": "input_register", "data_type": "float32", "owner": "process"}]}],
        "switches": [{"id": "SW"}],
        "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
                  {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
        "adapters": [adapter],
        "io_map": [{"adapter": adapter["id"], "external": {"var": var},
                    "device": "PLC1", "register": "temp", "direction": "in"}],
    }


SCENARIOS = [
    ("simulated sensor",
     {"id": "sim", "kind": "simulated", "spec": {"temp": {"wave": "constant", "value": VALUE}}}, "temp"),
    ("mock hardware driver",
     {"id": "hw", "kind": "hardware", "driver": "mock", "inputs": {"temp": VALUE}}, "temp"),
    ("process simulator",
     {"id": "proc", "kind": "simulator", "engine": "process",
      "model": {"level0": VALUE, "fill": 0.0, "drain": 0.0, "max": 100.0}}, "level"),
]


def run_scenario(core, label, adapter, var) -> None:
    net = corelive.build(core, cfg_for(adapter, var))
    try:
        # start() also launches the adapter host inside the PLC node, driving
        # the back channel
        net.start(cycles=10)
        time.sleep(3.0)
        temp = net.probe("MTU", "PLC1", "temp")
        check(f"{label}: MTU reads PLC1.temp == {VALUE} over Modbus",
              temp is not None and abs(temp - VALUE) < 0.05, f"temp={temp}")
    finally:
        net.stop()


def main() -> int:
    print("\n== Adapter interchangeability on live CORE (same PLC, three adapter kinds) ==")
    core = client.CoreGrpcClient(); core.connect()
    for label, adapter, var in SCENARIOS:
        run_scenario(core, label, adapter, var)
    print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
