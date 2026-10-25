#!/usr/bin/env python3
"""Live proof of register-based cross-PLC control (A2) + Modbus address-space
semantics.

Central MTU, two register-only PLCs, one register-logic rule:
    if PLC1.lvl (input_register) < 3  ->  set PLC2.pump (coil) = 1
The MTU reads PLC1's input register and writes PLC2's coil **over real Modbus,
node to node** — the centralized architecture, now register-based.

Also verifies standard Modbus address-space semantics on PLC1, which has BOTH an
``input_register`` and a ``holding_register`` at **address 0**:
  * a master WRITE to the holding register at address 0 SUCCEEDS (valid RW write,
    not rejected),
  * the input register at address 0 is a separate area: unchanged and read-only.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_logic.py

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
    "name": "logic_min", "control": {"placement": "mtu"},
    "mtu": {"id": "MTU", "ip": "10.0.0.254"},
    "devices": [
        {"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus", "registers": [
            {"id": "lvl",  "area": "input_register",   "address": 0, "data_type": "float32", "initial": 2.0},
            {"id": "setp", "area": "holding_register", "address": 0, "data_type": "uint16",  "initial": 0}]},
        {"id": "PLC2", "ip": "10.0.0.2", "protocol": "modbus", "registers": [
            {"id": "pump", "area": "coil", "data_type": "bool", "initial": False}]},
    ],
    "switches": [{"id": "SW"}],
    "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
              {"a": "PLC2", "b": "SW", "a_ip": "10.0.0.2"},
              {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
    "logic": [{"id": "r1",
               "if": [{"device": "PLC1", "register": "lvl", "op": "below", "value": 3.0}],
               "then": [{"device": "PLC2", "register": "pump", "set": 1}]}],
}


def probe_write(net, target, point, value):
    rc, out = net.nrun("MTU",
                       f'{corelive.PREFIX} && {corelive.NODE_PY} -m scadavt.core.probe '
                       f'--session {net.sess_json} --node {target} --point {point} '
                       f'--write {value} --timeout 5')
    return (rc == 0 and "wrote" in (out or "")), (out or "").strip().splitlines()[-1] if out else ""


def main() -> int:
    print("\n== Register-based cross-PLC control + Modbus address-space semantics ==")
    core = client.CoreGrpcClient(); core.connect()
    net = corelive.build(core, CFG)
    try:
        ok, warn = net.start(cycles=40)
        check("session started (central MTU + 2 register PLCs)", ok, f"warnings={warn}")

        # A2: the MTU's register logic drove PLC2.pump from PLC1.lvl over Modbus
        import time
        time.sleep(2.0)
        pump = net.probe("MTU", "PLC2", "pump")
        check("A2: cross-PLC register control — PLC2.pump opened from PLC1.lvl<3",
              pump == 1.0, f"pump={pump}")

        # Modbus address space: input_register@0 and holding_register@0 are
        # SEPARATE areas sharing the numeric address 0.
        lvl0 = net.probe("MTU", "PLC1", "lvl")
        check("input_register 'lvl' @0 reads 2.0", lvl0 == 2.0, f"lvl={lvl0}")
        wok, wout = probe_write(net, "PLC1", "setp", 42)
        check("valid HOLDING_register 'setp' @0 write SUCCEEDS (not rejected)", wok, wout)
        setp = net.probe("MTU", "PLC1", "setp")
        check("holding_register 'setp' @0 reads back 42", setp == 42.0, f"setp={setp}")
        lvl1 = net.probe("MTU", "PLC1", "lvl")
        check("input_register 'lvl' @0 unchanged by holding write (separate area)",
              lvl1 == 2.0, f"lvl={lvl1}")
        rok, rout = probe_write(net, "PLC1", "lvl", 9.0)
        check("master write to read-only input_register @0 is REJECTED", not rok, rout)
    finally:
        net.stop()

    print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
