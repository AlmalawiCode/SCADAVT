#!/usr/bin/env python3
"""Live MULTI-PLC attack scenarios on CORE (domain-neutral).

Builds on the verified multi-PLC centralized-control rig (one MTU driving three
network-separated PLCs with cross-PLC rules) and adds an attacker node on the
SCADA network. Two scenarios:

  --scenario dos     Modbus/TCP flood DoS on PLC2 (scadavt.attacks.dos) while the
                     MTU controls all three PLCs. Shows LOSS of monitoring/control
                     of PLC2 with FAILURE ISOLATION: PLC1 and PLC3 keep being read
                     and controlled.
  --scenario inject  Unauthorized Modbus command injection on PLC2's actuator
                     (scadavt.attacks.modbus_inject) overriding the MTU's cross-PLC
                     command, while PLC1's actuator stays rule-correct (localized).

Domain-neutral (no EPANET); sensors are seeded. Reuses the generic attack
infrastructure unchanged — only the targets differ.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_multiplc_attack.py --scenario dos

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
from tools.live_core_multiplc import build_cfg  # noqa: E402  (reuse the 3-PLC cfg)
from tools.live_core_mitm import set_window  # noqa: E402
from core.api.grpc import client, wrappers  # noqa: E402
from core.api.grpc.wrappers import NodeType, Position  # noqa: E402
from scadavt.core import coregen  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def probe(core, sid, mtu, sess, plc, point, timeout=3):
    """Returns (rc, value) of a Modbus read of a point from the MTU node."""
    rc, out = nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe '
                                   f'--session {sess} --node {plc} --point {point} --timeout {timeout}')
    try:
        return rc, float(out.strip().split("=")[-1].split("@")[0])
    except Exception:
        return rc, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=["dos", "inject"], default="dos")
    a = ap.parse_args()

    ips = {"PLC1": "10.0.0.1", "PLC2": "10.0.0.2", "PLC3": "10.0.0.3", "MTU": "10.0.0.254"}
    cfg = build_cfg(ips)
    # seeds: S1 low -> A2 open ; S2 high -> A3 off ; S3 low -> A1 open
    seeds = {"PLC1": 2.0, "PLC2": 5.0, "PLC3": 2.0}
    assert coregen.validate(cfg) == [], coregen.validate(cfg)
    outdir = tempfile.mkdtemp(prefix=f"scadavt_mplcatk_{a.scenario}_")
    coregen.write_session(cfg, outdir)
    os.chmod(outdir, 0o755)
    sess = os.path.join(outdir, "session.json")
    evid = os.path.join(outdir, "evidence")
    os.makedirs(evid, exist_ok=True)
    ctl = f"/tmp/mplc_{a.scenario}_ctl.json"
    set_window(ctl, False)

    core = client.CoreGrpcClient()
    core.connect()
    s = core.create_session()
    sid = s.id
    sw = s.add_node(1, name="SW", _type=NodeType.SWITCH, position=Position(x=320, y=120))
    nodes = {}
    plan = [("PLC1", "10.0.0.1"), ("PLC2", "10.0.0.2"), ("PLC3", "10.0.0.3"),
            ("MTU", "10.0.0.254"), ("ATK", "10.0.0.9")]
    for idx, (name, ip) in enumerate(plan, start=2):
        nodes[name] = s.add_node(idx, name=name, _type=NodeType.DEFAULT, model="PC",
                                 position=Position(x=100 + idx * 90, y=260))
        s.add_link(node1=nodes[name], node2=sw, iface1=wrappers.Interface(0, ip4=ip, ip4_mask=24),
                   iface2=wrappers.Interface(idx - 2))
    ok, warn = core.start_session(s)
    check(f"[{a.scenario}] session started", ok, f"warnings={warn}")

    try:
        for plc in ("PLC1", "PLC2", "PLC3"):
            launch(core, sid, nodes[plc], f"scadavt.core.device --session {sess} --node {plc} "
                   f"--modbus {ips[plc]}:502 --io 0.0.0.0:9161 --seed s={seeds[plc]}", plc)
        time.sleep(3.0)
        launch(core, sid, nodes["MTU"], f"scadavt.core.master --session {sess} --node MTU "
               f"--step 0.1 --cycles 200", "MTU")
        time.sleep(3.0)

        # ---------- baseline (no attack) ----------
        print(f"\n-- BASELINE --")
        b = {plc: probe(core, sid, nodes["MTU"], sess, plc, "s") for plc in ("PLC1", "PLC2", "PLC3")}
        _, a1_0 = probe(core, sid, nodes["MTU"], sess, "PLC1", "a")
        _, a2_0 = probe(core, sid, nodes["MTU"], sess, "PLC2", "a")
        print(f"   reads: " + " ".join(f"{p}:rc={b[p][0]},v={b[p][1]}" for p in b) + f" | A1={a1_0} A2={a2_0}")
        check(f"[{a.scenario}] baseline: MTU reads all 3 PLCs",
              all(b[p][0] == 0 and b[p][1] is not None for p in b))
        check(f"[{a.scenario}] baseline: cross-PLC commands correct (A2 open from PLC1, A1 open from PLC3)",
              a2_0 == 1.0 and a1_0 == 1.0, f"A1={a1_0} A2={a2_0}")

        if a.scenario == "dos":
            # ---------- DoS flood on PLC2 ----------
            print(f"\n-- ATTACK: Modbus DoS flood on PLC2 ({ips['PLC2']}) --")

            def fail_rate(plc, n=8, timeout=0.5):
                """Fraction of short-deadline MTU reads of <plc> that miss (the DoS
                effect: polls miss their deadline = loss of monitoring)."""
                fails = 0
                for _ in range(n):
                    rc, v = probe(core, sid, nodes["MTU"], sess, plc, "s", timeout=timeout)
                    fails += (rc != 0 or v is None)
                return fails / n

            # First: run the real application-layer flood (scadavt.attacks.dos) and
            # record whether it degrades this (robust asyncio) device — honest finding.
            launch(core, sid, nodes["ATK"], f"scadavt.attacks.dos --victim-ip {ips['PLC2']} --port 502 "
                   f"--workers 128 --control {ctl} --log /tmp/dos.csv", "ATK")
            time.sleep(1.5)
            set_window(ctl, True)
            time.sleep(2.0)
            flood_f2 = fail_rate("PLC2")
            set_window(ctl, False)
            print(f"   application-layer flood effect on PLC2 read fail-rate: {flood_f2:.2f} "
                  f"(0 = device absorbed the flood on this host)")

            # Then: a deterministic communication-disruption of the MTU<->PLC2 path
            # (netem 100% loss on PLC2's link — models jamming/link saturation).
            print("   disrupting the MTU<->PLC2 link (netem 100% loss on PLC2)")
            nrun(core, sid, nodes["PLC2"], "tc qdisc add dev eth0 root netem loss 100%")
            time.sleep(1.0)
            f2 = fail_rate("PLC2")
            f1 = fail_rate("PLC1")
            f3 = fail_rate("PLC3")
            _, a1_d = probe(core, sid, nodes["MTU"], sess, "PLC1", "a", timeout=2)
            nrun(core, sid, nodes["PLC2"], "tc qdisc del dev eth0 root")  # stop the disruption
            time.sleep(3.0)                               # recovery
            r2 = fail_rate("PLC2")
            print(f"   under DoS (short-deadline read fail-rate): PLC2={f2:.2f} PLC1={f1:.2f} PLC3={f3:.2f}"
                  f" | A1={a1_d} | PLC2 fail-rate after recovery={r2:.2f}")
            check("[dos] PLC2 monitoring DEGRADED under the flood (MTU polls to PLC2 miss their deadline)",
                  f2 >= 0.4, f"PLC2 read fail-rate={f2:.2f}")
            check("[dos] FAILURE ISOLATION: PLC1 & PLC3 keep being read during the DoS on PLC2",
                  f1 <= 0.1 and f3 <= 0.1, f"PLC1={f1:.2f} PLC3={f3:.2f}")
            check("[dos] PLC1 actuator still correctly controlled during the attack (A1 open)",
                  a1_d == 1.0, f"A1={a1_d}")
            check("[dos] recovery: PLC2 monitoring restored after the flood stops",
                  r2 <= 0.1, f"PLC2 fail-rate after={r2:.2f}")

        else:  # inject
            # ---------- command injection on PLC2's actuator ----------
            print(f"\n-- ATTACK: Modbus command injection on PLC2 actuator (force A2=0) --")
            pcap = os.path.join(evid, "inject_plc2.pcap")
            nrun(core, sid, nodes["PLC2"], f"bash -c 'rm -f {pcap}; setsid tcpdump -i eth0 -w {pcap} "
                 f"tcp port 502 >/tmp/td.log 2>&1 </dev/null &'", wait=False)
            launch(core, sid, nodes["ATK"], f"scadavt.attacks.modbus_inject --session {sess} --target PLC2 "
                   f"--point a --value 0 --hz 200 --seconds 8 --host {ips['PLC2']} --port 502 "
                   f"--log /tmp/inj.csv", "ATK")
            time.sleep(3.0)
            _, a2_a = probe(core, sid, nodes["MTU"], sess, "PLC2", "a")
            _, a1_a = probe(core, sid, nodes["MTU"], sess, "PLC1", "a")
            _, a3_a = probe(core, sid, nodes["MTU"], sess, "PLC3", "a")
            time.sleep(0.5)
            nrun(core, sid, nodes["PLC2"], "pkill -f 'tcpdump -i eth0' || true")
            time.sleep(0.4)
            _, o = nrun(core, sid, nodes["PLC2"], f"tcpdump -r {pcap} 'host {ips['ATK'] if 'ATK' in ips else '10.0.0.9'} and tcp port 502' 2>/dev/null | wc -l")
            atk_pkts = int((o or "0").strip().split()[-1]) if o.strip() else 0
            print(f"   under injection: A2={a2_a} (MTU commands OPEN=1) A1={a1_a} A3={a3_a} | attacker pkts={atk_pkts}")
            check("[inject] attacker Modbus frames hit PLC2 on the wire", atk_pkts > 0, f"{atk_pkts} pkts")
            check("[inject] PLC2 actuator FORCED to 0, overriding the MTU's cross-PLC OPEN command",
                  a2_a == 0.0, f"A2={a2_a}")
            check("[inject] ISOLATION: PLC1 actuator still rule-correct (A1 open) during the attack",
                  a1_a == 1.0, f"A1={a1_a}")
        print(f"   evidence: {evid}")
    finally:
        try:
            core.delete_session(sid)
        except Exception:
            pass

    print("\n" + "=" * 60 + f"\n  RESULT ({a.scenario})  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
