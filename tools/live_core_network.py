#!/usr/bin/env python3
"""Stand up an ARBITRARILY LARGE SCADA network on live CORE — the framework's
headline capability, driven entirely from the generic Python API.

This is the user-facing "project": a plain script that asks the framework for a
network of N PLCs (default 12) spread over several /24 subnets joined by a
router, plus one MTU, and then — with *no per-scenario topology code* — builds
it on real CORE, starts it, and proves it works:

    * the MTU reaches EVERY PLC (connectivity across all subnets / the WAN),
    * the MTU speaks Modbus to every PLC (per-PLC pcap),
    * the cross-PLC control ring fires: each PLC's low sensor opens the NEXT
      PLC's actuator, across subnet boundaries (centralized control at scale).

Scale it with --plcs / --per-subnet; the exact same code path runs 3 PLCs or 60.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_network.py --plcs 12

Needs a running core-daemon. Exit 0 only if all checks pass.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.api.grpc import client  # noqa: E402
from scadavt.core import corelive  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def manual_cfg() -> dict:
    """A hand-written, NON-grid topology to prove the engine is config-driven,
    not grid-driven. Asymmetric: subnet A (10.0.1.0/24) holds the MTU + 2 PLCs,
    subnet B (10.0.2.0/24) holds 2 PLCs, joined by a router. Cross-PLC rules are
    NOT a ring: PLC1 (A, low) opens PLC3 (B); PLC3 (B, low) opens PLC2 (A);
    PLC4 (B, high) touches nothing. This exercises arbitrary links + rules."""
    return {
        "name": "manual_asym", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.1.254"},
        "devices": [
            {"id": "PLC1", "ip": "10.0.1.1", "protocol": "modbus", "seed": 2.0,
             "points": [{"id": "s", "role": "measurement"}, {"id": "a", "role": "command", "is_bool": True}]},
            {"id": "PLC2", "ip": "10.0.1.2", "protocol": "modbus", "seed": 5.0,
             "points": [{"id": "s", "role": "measurement"}, {"id": "a", "role": "command", "is_bool": True}]},
            {"id": "PLC3", "ip": "10.0.2.1", "protocol": "modbus", "seed": 2.0,
             "points": [{"id": "s", "role": "measurement"}, {"id": "a", "role": "command", "is_bool": True}]},
            {"id": "PLC4", "ip": "10.0.2.2", "protocol": "modbus", "seed": 5.0,
             "points": [{"id": "s", "role": "measurement"}, {"id": "a", "role": "command", "is_bool": True}]},
        ],
        "switches": [{"id": "SWA"}, {"id": "SWB"}],
        "routers": [{"id": "R1", "model": "router"}],
        "links": [
            {"a": "R1", "b": "SWA", "a_ip": "10.0.1.250"},
            {"a": "R1", "b": "SWB", "a_ip": "10.0.2.250"},
            {"a": "MTU", "b": "SWA", "a_ip": "10.0.1.254"},
            {"a": "PLC1", "b": "SWA", "a_ip": "10.0.1.1"},
            {"a": "PLC2", "b": "SWA", "a_ip": "10.0.1.2"},
            {"a": "PLC3", "b": "SWB", "a_ip": "10.0.2.1"},
            {"a": "PLC4", "b": "SWB", "a_ip": "10.0.2.2"},
        ],
        "mapping": [
            {"point": "s", "device": "PLC1", "role": "measurement", "element": "S1"},
            {"point": "a", "device": "PLC1", "role": "command", "element": "A1"},
            {"point": "s", "device": "PLC2", "role": "measurement", "element": "S2"},
            {"point": "a", "device": "PLC2", "role": "command", "element": "A2"},
            {"point": "s", "device": "PLC3", "role": "measurement", "element": "S3"},
            {"point": "a", "device": "PLC3", "role": "command", "element": "A3"},
            {"point": "s", "device": "PLC4", "role": "measurement", "element": "S4"},
            {"point": "a", "device": "PLC4", "role": "command", "element": "A4"},
        ],
        "rules": [
            {"id": "r1", "if": [{"kind": "sensor", "comp": "S1", "attr": "value", "op": "below", "value": 3.0}],
             "then": [{"comp": "A3", "kind": "pump", "attr": "status", "value": "open"}]},
            {"id": "r2", "if": [{"kind": "sensor", "comp": "S3", "attr": "value", "op": "below", "value": 3.0}],
             "then": [{"comp": "A2", "kind": "pump", "attr": "status", "value": "open"}]},
        ],
        "_expect": {"A3": 1.0, "A2": 1.0, "A1": 0.0, "A4": 0.0},
        "_plc_ips": {"PLC1": "10.0.1.1", "PLC2": "10.0.1.2", "PLC3": "10.0.2.1", "PLC4": "10.0.2.2"},
    }


def run_manual(core) -> None:
    cfg = manual_cfg()
    expect = cfg["_expect"]
    plc_ips = cfg["_plc_ips"]
    print("\n== NON-GRID manual topology (asymmetric 2+2 across a router) ==")
    net = corelive.build(core, cfg)
    try:
        ok, warn = net.start(cycles=80)
        check("manual: session started", ok, f"warnings={warn}")
        reach = sum(net.ping("MTU", ip) for ip in plc_ips.values())
        check("manual: MTU reaches all 4 PLCs (2 subnets)", reach == 4, f"{reach}/4")
        evid = os.path.join(net.workdir, "evidence")
        os.makedirs(evid, exist_ok=True)
        pcap = os.path.join(evid, "manual_mtu.pcap")
        net.capture_start("MTU", pcap)
        answered = sum(net.probe("MTU", p, "s") is not None for p in plc_ips)
        net.capture_stop("MTU")
        check("manual: MTU read a Modbus point from all 4 PLCs", answered == 4, f"{answered}/4")
        talked = sum(net.talked_to("MTU", pcap, ip) for ip in plc_ips.values())
        check("manual: MTU spoke Modbus to all 4 PLCs (pcap evidence)", talked == 4, f"{talked}/4")
        time.sleep(2.0)
        for elem, want in expect.items():
            plc = {"A1": "PLC1", "A2": "PLC2", "A3": "PLC3", "A4": "PLC4"}[elem]
            got = net.probe("MTU", plc, "a")
            check(f"manual: {elem} == {want} (non-ring cross-PLC rule)", got == want, f"{elem}={got}")
        print(f"   evidence: {evid}")
    finally:
        net.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plcs", type=int, default=12, help="number of PLC nodes")
    ap.add_argument("--per-subnet", type=int, default=6, help="PLCs per /24 subnet")
    ap.add_argument("--sample", type=int, default=6,
                    help="how many PLCs to spot-check for cross-PLC control")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--manual", action="store_true",
                    help="run the hand-written non-grid topology instead of a grid")
    a = ap.parse_args(argv)

    if a.manual:
        core = client.CoreGrpcClient()
        core.connect()
        run_manual(core)
        print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
        for f in FAIL:
            print(f"    FAIL -> {f}")
        return 0 if not FAIL else 1

    cfg = corelive.grid(a.plcs, per_subnet=a.per_subnet)
    plc_ips = cfg["_plc_ips"]
    n_sub = len(set(cfg["_seg_of"].values()))
    print(f"\n== Building a {a.plcs}-PLC network on live CORE "
          f"({n_sub} subnet(s), 1 router, 1 MTU) ==")

    core = client.CoreGrpcClient()
    core.connect()
    net = corelive.build(core, cfg)
    try:
        ok, warn = net.start(cycles=80)
        check(f"session started ({a.plcs} PLCs + MTU + router + {n_sub} switches)",
              ok, f"warnings={warn}")

        # 1) connectivity: the MTU reaches EVERY PLC (across subnets / the WAN)
        reach = sum(net.ping("MTU", plc_ips[f"PLC{i}"]) for i in range(1, a.plcs + 1))
        check(f"MTU reaches ALL {a.plcs} PLCs (IP connectivity across {n_sub} subnet(s))",
              reach == a.plcs, f"{reach}/{a.plcs} reachable")

        # 2) Modbus reachability: capture on the MTU interface while the MTU
        #    reads a point from EVERY PLC — deterministic, independent of the
        #    master's run window.
        evid = os.path.join(net.workdir, "evidence")
        os.makedirs(evid, exist_ok=True)
        pcap = os.path.join(evid, "mtu_modbus.pcap")
        net.capture_start("MTU", pcap)
        reads = {}
        for i in range(1, a.plcs + 1):
            reads[i] = net.probe("MTU", f"PLC{i}", "s")      # Modbus read MTU -> PLC i
        net.capture_stop("MTU")
        answered = sum(v is not None for v in reads.values())
        check(f"MTU read a Modbus point from ALL {a.plcs} PLCs", answered == a.plcs,
              f"{answered}/{a.plcs} answered")
        talked = sum(net.talked_to("MTU", pcap, plc_ips[f"PLC{i}"]) for i in range(1, a.plcs + 1))
        check(f"MTU spoke Modbus to ALL {a.plcs} PLCs (pcap evidence)", talked == a.plcs,
              f"{talked}/{a.plcs} PLCs in pcap")

        # 3) cross-PLC control ring: PLC k (odd => low sensor) opens PLC k+1's
        #    actuator. Spot-check `sample` odd-indexed PLCs and their targets.
        time.sleep(2.0)
        good = 0
        checked = 0
        for i in range(1, a.plcs + 1):
            if i % 2 == 1:                       # odd PLC seeded LOW -> fires its rule
                checked += 1
                if checked > a.sample:
                    break
                tgt = i % a.plcs + 1
                a_val = net.probe("MTU", f"PLC{tgt}", "a")
                opened = (a_val == 1.0)
                good += opened
                check(f"cross-PLC: PLC{i} low sensor opened PLC{tgt} actuator",
                      opened, f"A(PLC{tgt})={a_val}")
        check(f"cross-PLC control ring fired for all {min(a.sample, checked)} sampled PLCs",
              good == min(a.sample, checked), f"{good}/{min(a.sample, checked)}")
        print(f"   evidence: {evid}")
    finally:
        if not a.keep:
            net.stop()

    print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
