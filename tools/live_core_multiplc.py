#!/usr/bin/env python3
"""Live multi-PLC centralized (MTU) control regression on real CORE.

Validates the core requirement: ONE MTU reads measurements from SEVERAL
network-separated PLCs, evaluates CROSS-PLC rules, and commands actuators on
OTHER PLCs. Domain-neutral (no EPANET) so it exercises the generic framework.

Three PLCs, each with a sensor (measurement) and an actuator (command). The
sensors are seeded to known values; the MTU master drives all three. CROSS-PLC
control ring:
    S1 (PLC1) low  -> command A2 (PLC2)
    S2 (PLC2) high -> A3 (PLC3) stays off
    S3 (PLC3) low  -> command A1 (PLC1)
So each actuator is commanded from *another* PLC's reading — proving read-from-
different-PLCs + cross-PLC rule + command-other-PLC.

Run on both topologies:
    --topo lan   all PLCs + MTU on one switch (same subnet)
    --topo wan   PLC2/PLC3 on a second subnet, reached via a router
    (default: run both)

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_multiplc.py

Needs a running core-daemon. Exit 0 only if all checks pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools.live_core_closed_loop import NODE_PY, PREFIX, launch, nrun  # noqa: E402
from core.api.grpc import client, wrappers  # noqa: E402
from core.api.grpc.wrappers import NodeType, Position  # noqa: E402
from scadavt.core import coregen  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def build_cfg(ips):
    """ips = {PLC1:ip, PLC2:ip, PLC3:ip, MTU:ip}. Each PLC: sensor 's' + actuator 'a'."""
    devices, mapping = [], []
    for i, plc in enumerate(("PLC1", "PLC2", "PLC3"), start=1):
        devices.append({"id": plc, "ip": ips[plc], "protocol": "modbus",
                        "points": [{"id": "s", "role": "measurement", "is_bool": False},
                                   {"id": "a", "role": "command", "is_bool": True}]})
        mapping += [{"point": "s", "device": plc, "role": "measurement", "element": f"S{i}"},
                    {"point": "a", "device": plc, "role": "command", "element": f"A{i}"}]
    rules = [  # cross-PLC ring (kind/attr are ignored by the value reader — domain-neutral)
        {"id": "r1", "if": [{"kind": "sensor", "comp": "S1", "attr": "value", "op": "below", "value": 3.0}],
         "then": [{"comp": "A2", "kind": "pump", "attr": "status", "value": "open"}]},
        {"id": "r2", "if": [{"kind": "sensor", "comp": "S2", "attr": "value", "op": "below", "value": 3.0}],
         "then": [{"comp": "A3", "kind": "pump", "attr": "status", "value": "open"}]},
        {"id": "r3", "if": [{"kind": "sensor", "comp": "S3", "attr": "value", "op": "below", "value": 3.0}],
         "then": [{"comp": "A1", "kind": "pump", "attr": "status", "value": "open"}]},
    ]
    return {"name": "multiplc", "control": {"placement": "mtu"},
            "mtu": {"id": "MTU", "ip": ips["MTU"]},
            "devices": devices, "mapping": mapping, "rules": rules}


def probe(core, sid, mtu, sess, plc, point):
    _, out = nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe '
                                  f'--session {sess} --node {plc} --point {point} --timeout 5')
    try:
        return float(out.strip().split("=")[-1].split("@")[0])
    except Exception:
        return None


def run_topology(core, topo):
    print("\n" + "=" * 64 + f"\n  TOPOLOGY: {topo.upper()}\n" + "=" * 64)
    if topo == "lan":
        ips = {"PLC1": "10.0.0.1", "PLC2": "10.0.0.2", "PLC3": "10.0.0.3", "MTU": "10.0.0.254"}
    else:  # wan: MTU+PLC1 on subnet A; PLC2,PLC3 on subnet B via a router
        ips = {"PLC1": "10.0.1.1", "MTU": "10.0.1.254", "PLC2": "10.0.2.1", "PLC3": "10.0.2.2"}
    cfg = build_cfg(ips)
    assert coregen.validate(cfg) == [], coregen.validate(cfg)
    outdir = tempfile.mkdtemp(prefix=f"scadavt_mplc_{topo}_")
    coregen.write_session(cfg, outdir)
    os.chmod(outdir, 0o755)
    sess = os.path.join(outdir, "session.json")
    evid = os.path.join(outdir, "evidence")
    os.makedirs(evid, exist_ok=True)

    s = core.create_session()
    sid = s.id
    nodes = {}
    if topo == "lan":
        sw = s.add_node(1, name="SW", _type=NodeType.SWITCH, position=Position(x=300, y=120))
        plan = [("PLC1", "10.0.0.1"), ("PLC2", "10.0.0.2"), ("PLC3", "10.0.0.3"), ("MTU", "10.0.0.254")]
        nid = 2
        for name, ip in plan:
            nodes[name] = s.add_node(nid, name=name, _type=NodeType.DEFAULT, model="PC",
                                     position=Position(x=120 + nid * 90, y=260))
            s.add_link(node1=nodes[name], node2=sw,
                       iface1=wrappers.Interface(0, ip4=ip, ip4_mask=24),
                       iface2=wrappers.Interface(nid - 2))
            nid += 1
        routes = []
    else:
        swa = s.add_node(1, name="SW_A", _type=NodeType.SWITCH, position=Position(x=200, y=120))
        swb = s.add_node(2, name="SW_B", _type=NodeType.SWITCH, position=Position(x=520, y=120))
        rtr = s.add_node(3, name="R1", _type=NodeType.DEFAULT, model="router", position=Position(x=360, y=120))
        specs = [("MTU", "10.0.1.254", swa, "a"), ("PLC1", "10.0.1.1", swa, "a"),
                 ("PLC2", "10.0.2.1", swb, "b"), ("PLC3", "10.0.2.2", swb, "b")]
        nid, ac, bc = 4, 1, 1
        for name, ip, sw, side in specs:
            nodes[name] = s.add_node(nid, name=name, _type=NodeType.DEFAULT, model="PC",
                                     position=Position(x=120 + nid * 80, y=260))
            swc = ac if side == "a" else bc
            s.add_link(node1=nodes[name], node2=sw, iface1=wrappers.Interface(0, ip4=ip, ip4_mask=24),
                       iface2=wrappers.Interface(swc))
            if side == "a":
                ac += 1
            else:
                bc += 1
            nid += 1
        # router legs
        s.add_link(node1=rtr, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.1.250", ip4_mask=24),
                   iface2=wrappers.Interface(ac))
        s.add_link(node1=rtr, node2=swb, iface1=wrappers.Interface(1, ip4="10.0.2.250", ip4_mask=24),
                   iface2=wrappers.Interface(bc))
        routes = [("R1", rtr, "sysctl -w net.ipv4.ip_forward=1"),
                  ("MTU", nodes["MTU"], "ip route replace default via 10.0.1.250"),
                  ("PLC1", nodes["PLC1"], "ip route replace default via 10.0.1.250"),
                  ("PLC2", nodes["PLC2"], "ip route replace default via 10.0.2.250"),
                  ("PLC3", nodes["PLC3"], "ip route replace default via 10.0.2.250")]
    ok, warn = core.start_session(s)
    check(f"[{topo}] session started", ok, f"warnings={warn}")
    try:
        for _, node, cmd in routes:
            nrun(core, sid, node, cmd)
        if routes:
            time.sleep(1.0)
        # connectivity: MTU reaches every PLC
        for plc in ("PLC1", "PLC2", "PLC3"):
            rc, _ = nrun(core, sid, nodes["MTU"], f"ping -c2 -W1 {ips[plc]}")
            check(f"[{topo}] MTU -> {plc} reachable ({ips[plc]})", rc == 0)
        # launch each PLC device, seeding its sensor (S1=2 low, S2=5 high, S3=2 low)
        seeds = {"PLC1": 2.0, "PLC2": 5.0, "PLC3": 2.0}
        for plc in ("PLC1", "PLC2", "PLC3"):
            launch(core, sid, nodes[plc], f"scadavt.core.device --session {sess} --node {plc} "
                   f"--modbus {ips[plc]}:502 --io 0.0.0.0:9161 --seed s={seeds[plc]}", plc)
        time.sleep(3.0)
        # the MTU master drives ALL three PLCs with the cross-PLC rules
        launch(core, sid, nodes["MTU"], f"scadavt.core.master --session {sess} --node MTU "
               f"--step 0.1 --cycles 60", "MTU")
        # capture Modbus on the MTU interface (shows it talks to all three PLCs)
        pcap = os.path.join(evid, f"{topo}_mtu.pcap")
        nrun(core, sid, nodes["MTU"], f"bash -c 'rm -f {pcap}; setsid tcpdump -i eth0 -w {pcap} "
             f"tcp port 502 >/tmp/td.log 2>&1 </dev/null &'", wait=False)
        time.sleep(5.0)
        nrun(core, sid, nodes["MTU"], "pkill -f 'tcpdump -i eth0' || true")
        time.sleep(0.4)

        # read back each actuator OVER MODBUS (from the MTU, across the network)
        a1 = probe(core, sid, nodes["MTU"], sess, "PLC1", "a")   # driven by S3 (PLC3) low -> open
        a2 = probe(core, sid, nodes["MTU"], sess, "PLC2", "a")   # driven by S1 (PLC1) low -> open
        a3 = probe(core, sid, nodes["MTU"], sess, "PLC3", "a")   # driven by S2 (PLC2) high -> off
        print(f"   actuators: A1(PLC1)={a1}  A2(PLC2)={a2}  A3(PLC3)={a3}")

        # how many of the 3 PLC IPs did the MTU actually talk Modbus to?
        talked = 0
        for plc in ("PLC1", "PLC2", "PLC3"):
            _, o = nrun(core, sid, nodes["MTU"], f"tcpdump -r {pcap} 'host {ips[plc]}' 2>/dev/null | wc -l")
            n = int((o or "0").strip().split()[-1]) if o.strip() else 0
            talked += n > 0
        check(f"[{topo}] MTU communicated Modbus with all 3 PLCs", talked == 3, f"{talked}/3 PLCs")
        check(f"[{topo}] cross-PLC: A2 commanded OPEN from PLC1's low sensor", a2 == 1.0, f"A2={a2}")
        check(f"[{topo}] cross-PLC: A1 commanded OPEN from PLC3's low sensor", a1 == 1.0, f"A1={a1}")
        check(f"[{topo}] cross-PLC: A3 stays OFF (PLC2 sensor high)", a3 == 0.0, f"A3={a3}")
        print(f"   evidence: {evid}")
    finally:
        try:
            core.delete_session(sid)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topo", choices=["lan", "wan", "both"], default="both")
    a = ap.parse_args()
    core = client.CoreGrpcClient()
    core.connect()
    for topo in (["lan", "wan"] if a.topo == "both" else [a.topo]):
        run_topology(core, topo)
    print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
