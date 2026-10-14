#!/usr/bin/env python3
"""Live integrity-attack experiment on real CORE: benign baseline vs a real
Modbus command-injection attack from an attacker node on the SCADA network.

Topology (adds ATK to the verified closed-loop):
    SCADA LAN 10.0.0.0/24 :  MTU(.254)  PLC1(.1)  ATK(.9)   <- attacker here
    back channel 172.16.0.0/24 :  APP(.254)  PLC1(.1)

Two phases on one session (EPANET state carries over, so the attack's drop is
measured against the benign level it just held):
  1. BENIGN  — MTU controls the pump (speed 0.7); record baseline trace.
  2. ATTACK  — ATK injects real Modbus writes of 0.0 into PLC1's command register
               while the MTU keeps commanding 0.7; record the divergence + impact.

Nothing in EPANET or the gateway is modified — only Modbus traffic on the wire.
Collects synchronized PCAP (per phase), actuator three-way values, hydraulic
traces, and the attacker's own write log, then classifies the outcome.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_attack.py

Needs a running core-daemon and numpy+epanet in the node python. Exit 0 if the
attack is verified (real on-wire manipulation + divergence + physical impact).
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools.live_core_closed_loop import (NODE_PY, PREFIX, launch, nrun,  # noqa: E402
                                         read_level)
from core.api.grpc import client, wrappers  # noqa: E402
from core.api.grpc.wrappers import NodeType, Position  # noqa: E402
from scadavt.core import coregen  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def three_way(core, sid, mtu, appn, sess):
    """Independently read the three stages: commanded (MTU log), received (PLC
    register over Modbus), applied (EPANET setting over the Integration API)."""
    _, cmdout = nrun(core, sid, mtu, "grep CMD /tmp/MTU.log | tail -1")
    commanded = None
    if "{" in (cmdout or ""):
        try:
            commanded = json.loads(cmdout[cmdout.index("{"):].strip()).get("P1")
        except Exception:
            commanded = None
    _, pr = nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe '
                                 f'--session {sess} --node PLC1 --point P1_SPD')
    try:
        received = float(pr.strip().split("=")[-1].split("@")[0])
    except Exception:
        received = None
    applied = read_level(core, sid, appn, ("172.16.0.254", 9000), point="P1_SET")
    return commanded, received, applied


def sample_trace(core, sid, appn, mtu, sess, n, dt=0.5):
    """Sample the window: (t, tank level, applied setting, received register)."""
    rows, t0 = [], time.time()
    for _ in range(n):
        lvl = read_level(core, sid, appn, ("172.16.0.254", 9000), "T1_LEV")
        setg = read_level(core, sid, appn, ("172.16.0.254", 9000), "P1_SET")
        _, pr = nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe '
                                     f'--session {sess} --node PLC1 --point P1_SPD')
        try:
            recv = float(pr.strip().split("=")[-1].split("@")[0])
        except Exception:
            recv = None
        rows.append((round(time.time() - t0, 2), lvl, setg, recv))
        time.sleep(dt)
    return rows


def _levels(rows):
    return [r[1] for r in rows if r[1] is not None]


def _frac_zero(rows, idx):
    vals = [r[idx] for r in rows if r[idx] is not None]
    return (sum(v < 0.1 for v in vals) / len(vals)) if vals else 0.0


def main():
    cfg = json.loads(open(os.path.join(REPO, "scadavt", "scenarios",
                                        "closed_loop_min", "lab_speed.json")).read())
    cfg["devices"][0]["ip"] = "10.0.0.1"
    cfg["devices"][0]["backchannel"] = "172.16.0.1:9161"
    import tempfile
    outdir = tempfile.mkdtemp(prefix="scadavt_atk_")
    coregen.write_session(cfg, outdir)
    os.chmod(outdir, 0o755)
    sess = os.path.join(outdir, "session.json")
    open(os.path.join(outdir, "lab.json"), "w").write(json.dumps(cfg))
    labpath = os.path.join(outdir, "lab.json")
    evid = os.path.join(outdir, "evidence")
    os.makedirs(evid, exist_ok=True)

    core = client.CoreGrpcClient()
    core.connect()
    s = core.create_session()
    sid = s.id
    sw_s = s.add_node(1, name="SW_SCADA", _type=NodeType.SWITCH, position=Position(x=250, y=120))
    sw_m = s.add_node(2, name="SW_MGMT", _type=NodeType.SWITCH, position=Position(x=250, y=380))
    plc = s.add_node(3, name="PLC1", _type=NodeType.DEFAULT, model="PC", position=Position(x=150, y=240))
    mtu = s.add_node(4, name="MTU", _type=NodeType.DEFAULT, model="PC", position=Position(x=400, y=120))
    appn = s.add_node(5, name="APP", _type=NodeType.DEFAULT, model="PC", position=Position(x=400, y=380))
    atk = s.add_node(6, name="ATK", _type=NodeType.DEFAULT, model="PC", position=Position(x=100, y=120))
    s.add_link(node1=plc, node2=sw_s, iface1=wrappers.Interface(0, ip4="10.0.0.1", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=mtu, node2=sw_s, iface1=wrappers.Interface(0, ip4="10.0.0.254", ip4_mask=24), iface2=wrappers.Interface(1))
    s.add_link(node1=atk, node2=sw_s, iface1=wrappers.Interface(0, ip4="10.0.0.9", ip4_mask=24), iface2=wrappers.Interface(2))
    s.add_link(node1=plc, node2=sw_m, iface1=wrappers.Interface(1, ip4="172.16.0.1", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=appn, node2=sw_m, iface1=wrappers.Interface(0, ip4="172.16.0.254", ip4_mask=24), iface2=wrappers.Interface(1))
    ok, warn = core.start_session(s)
    check("attack-scenario session started", ok, f"warnings={warn}")

    N = 140          # gateway/master cycles (~14s at --sleep 0.1) — spans the whole window
    try:
        # device + EPANET app (persist across both phases)
        launch(core, sid, plc, f"scadavt.core.device --session {sess} --node PLC1 "
                               f"--modbus 10.0.0.1:502 --io 0.0.0.0:9161", "PLC1")
        launch(core, sid, appn, f"scadavt.examples.app_runner --config {labpath} "
                                f"--listen 172.16.0.254:9000", "APP")
        time.sleep(4.0)
        check("ATK can reach PLC1 over the SCADA network",
              nrun(core, sid, atk, "ping -c2 -W1 10.0.0.1")[0] == 0)

        def pcap_start(tag):
            p = os.path.join(evid, f"{tag}.pcap")
            nrun(core, sid, plc, f"bash -c 'rm -f {p}; setsid tcpdump -i eth0 -w {p} tcp port 502 "
                                 f">/tmp/td.log 2>&1 </dev/null &'", wait=False)
            return p

        def pcap_stop(p):
            nrun(core, sid, plc, "pkill -f 'tcpdump -i eth0' || true")
            time.sleep(0.4)
            _, o = nrun(core, sid, plc, f"tcpdump -r {p} 2>/dev/null | wc -l")
            return int((o or "0").strip().split()[-1]) if o.strip() else 0

        def start_loop():
            launch(core, sid, appn, f"scadavt.core.gateway_run --session {sess} --app 172.16.0.254:9000 "
                                    f"--step 120 --cycles {N} --sleep 0.1", "GW")
            launch(core, sid, mtu, f"scadavt.core.master --session {sess} --node MTU --step 0.1 --cycles {N}", "MTU")

        def stop_loop():
            nrun(core, sid, appn, "pkill -f scadavt.core.gateway_run || true")
            nrun(core, sid, mtu, "pkill -f scadavt.core.master || true")
            nrun(core, sid, atk, "pkill -f scadavt.attacks.modbus_inject || true")
            time.sleep(1.0)

        # ---------------- PHASE 1: BENIGN baseline ----------------
        print("\n-- PHASE 1: benign baseline --")
        pb = pcap_start("benign")
        start_loop()
        time.sleep(2.0)                                    # let control settle
        benign = sample_trace(core, sid, appn, mtu, sess, 10)   # ~5s window, loop still stepping
        b_cmd, b_recv, b_appl = three_way(core, sid, mtu, appn, sess)
        n_benign = pcap_stop(pb)
        stop_loop()
        benign_min = min(_levels(benign)) if _levels(benign) else None
        benign_end = benign[-1][1] if benign else None
        check("benign: pump commanded and applied, register not forced to 0",
              b_cmd is not None and b_recv is not None and b_appl is not None
              and abs(b_cmd - b_recv) < 1e-3 and abs(b_appl - b_recv) < 1e-3
              and _frac_zero(benign, 3) < 0.2,
              f"commanded={b_cmd} received={b_recv} applied={b_appl} recv0frac={_frac_zero(benign,3):.2f}")

        # ---------------- PHASE 2: ATTACK ----------------
        print("\n-- PHASE 2: Modbus command-injection attack --")
        pa = pcap_start("attack")
        start_loop()
        launch(core, sid, atk, f"scadavt.attacks.modbus_inject --session {sess} --target PLC1 "
                               f"--point P1_SPD --value 0.0 --hz 300 --seconds 12 --host 10.0.0.1 "
                               f"--port 502 --log /tmp/atk.csv", "ATK")
        time.sleep(2.0)
        attack = sample_trace(core, sid, appn, mtu, sess, 10)
        a_cmd, a_recv, a_appl = three_way(core, sid, mtu, appn, sess)
        n_attack = pcap_stop(pa)
        _, atkpkts = nrun(core, sid, plc, f"tcpdump -r {pa} 'host 10.0.0.9 and tcp port 502' 2>/dev/null | wc -l")
        n_atk_pkts = int((atkpkts or "0").strip().split()[-1]) if atkpkts.strip() else 0
        stop_loop()

        attack_min = min(_levels(attack)) if _levels(attack) else None
        recv0 = _frac_zero(attack, 3)          # fraction of samples the register was forced to 0
        appl0 = _frac_zero(attack, 2)          # fraction of samples EPANET applied ~0

        # ---------------- classification ----------------
        print("\n-- evidence --")
        print(f"   benign : cmd={b_cmd} recv={b_recv} appl={b_appl} level min~{benign_min} end~{benign_end}")
        print(f"   attack : cmd={a_cmd} recv0frac={recv0:.2f} appl0frac={appl0:.2f} level min~{attack_min}")
        print(f"   pcap   : benign={n_benign} pkts, attack={n_attack} pkts, attacker->PLC={n_atk_pkts} pkts")

        check("attack manipulated ACTUAL Modbus traffic (attacker frames to PLC on the wire)",
              n_atk_pkts > 0, f"{n_atk_pkts} attacker Modbus packets in pcap")
        check("divergence: controller keeps commanding pump ON but attack forces the "
              "register/applied value to 0 most of the window",
              a_cmd is not None and a_cmd > 0.5 and recv0 > 0.6 and appl0 > 0.5,
              f"commanded={a_cmd} recv0frac={recv0:.2f} appl0frac={appl0:.2f}")
        impact = (benign_min is not None and attack_min is not None
                  and attack_min < benign_min - 0.5)
        check("physical impact: tank drained lower under attack than the benign baseline",
              impact, f"benign min~{benign_min} vs attack min~{attack_min}")
        check("causality: physical drop coincides with on-wire attacker frames forcing the register",
              impact and n_atk_pkts > 0 and recv0 > 0.6)

        # write synchronized evidence
        for name, rows in [("benign_trace.csv", benign), ("attack_trace.csv", attack)]:
            with open(os.path.join(evid, name), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["wall_s", "T1_level_m", "P1_applied_setting", "P1_received_register"])
                w.writerows(rows)
        import shutil
        for src, dst in [("/tmp/atk.csv", "attacker_writes.csv"), ("/tmp/MTU.log", "commanded_mtu.log"),
                         ("/tmp/GW.log", "gateway.log")]:
            try:
                shutil.copy(src, os.path.join(evid, dst))
            except Exception:
                pass
    finally:
        try:
            core.delete_session(sid)
        except Exception:
            pass

    print(f"\n  evidence: {evid}")
    print("=" * 60 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
