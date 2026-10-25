#!/usr/bin/env python3
"""Live proof of the register-based PLC model (A1 + A8).

Builds a PLC defined with **only Modbus registers** — no sensor/actuator, no
role — and runs it on real CORE:

* **A1** — the MTU reads the PLC's ``input_register`` and reads/writes its
  ``coil`` over actual Modbus at the configured addresses.
* **A8** — a master write to the read-only ``input_register`` is **rejected**
  (standard Modbus read-only semantics: only the device's internal producer sets
  it; here via the register's ``initial`` value).

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_registers.py

Needs a running core-daemon. Exit 0 only if all checks pass.
"""

from __future__ import annotations

import os
import sys

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.api.grpc import client  # noqa: E402
from scadavt.core import corelive  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


CFG = {
    "name": "registers_min", "control": {"placement": "mtu"},
    "mtu": {"id": "MTU", "ip": "10.0.0.254"},
    "devices": [{
        "id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus",
        # a pure Modbus register device — NO sensor/actuator, NO role
        "registers": [
            {"id": "lvl",  "area": "input_register", "data_type": "float32", "initial": 4.5},
            {"id": "pump", "area": "coil",           "data_type": "bool",    "initial": False},
        ],
    }],
    "switches": [{"id": "SW"}],
    "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
              {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
}


def probe_write(net, target, point, value):
    """Run a Modbus write from the MTU; return (ok, output)."""
    rc, out = net.nrun("MTU",
                       f'{corelive.PREFIX} && {corelive.NODE_PY} -m scadavt.core.probe '
                       f'--session {net.sess_json} --node {target} --point {point} '
                       f'--write {value} --timeout 5')
    return (rc == 0 and "wrote" in (out or "")), (out or "").strip()


def main() -> int:
    assert not any("role" in r for d in CFG["devices"] for r in d["registers"]), \
        "config must be pure registers"
    print("\n== Pure register PLC on live CORE (input_register + coil, no sensor/actuator) ==")
    core = client.CoreGrpcClient(); core.connect()
    net = corelive.build(core, CFG)
    try:
        ok, warn = net.start(cycles=30)
        check("session started", ok, f"warnings={warn}")
        check("MTU -> PLC1 reachable", net.ping("MTU", "10.0.0.1"))

        # A1: read the input register over Modbus (producer set it via `initial`)
        lvl = net.probe("MTU", "PLC1", "lvl")
        check("A1: MTU reads input_register 'lvl' over Modbus == 4.5", lvl == 4.5, f"lvl={lvl}")

        # A1: write + read the coil over Modbus
        wok, wout = probe_write(net, "PLC1", "pump", 1)
        check("A1: MTU writes coil 'pump'=1 over Modbus", wok, wout)
        pump = net.probe("MTU", "PLC1", "pump")
        check("A1: MTU reads coil 'pump' back == 1", pump == 1.0, f"pump={pump}")

        # A8: a master write to the read-only input register is rejected
        rok, rout = probe_write(net, "PLC1", "lvl", 9.0)
        check("A8: master write to read-only input_register is REJECTED", not rok, rout)
        lvl2 = net.probe("MTU", "PLC1", "lvl")
        check("A8: input_register 'lvl' unchanged after rejected write == 4.5", lvl2 == 4.5, f"lvl={lvl2}")
    finally:
        net.stop()

    print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
