#!/usr/bin/env python3
"""Live closed-loop EPANET+SCADA integration test on real CORE 9.x namespaces.

The complete cycle, distributed across CORE nodes exactly as the architecture
intends — EPANET is an INDEPENDENT external application on the out-of-band back
channel; the SCADA network carries only Modbus:

    SCADA LAN  10.0.0.0/24 :  MTU(10.0.0.254)  <--Modbus-->  PLC1(10.0.0.1)
    back-channel 172.16.0.0/24 :  APP(172.16.0.254)  <--IOModules-->  PLC1(172.16.0.1)

  - APP node runs the EPANET app (Integration API) + the gateway (bridges app<->PLC
    and drives EPANET's clock). EPANET's hydraulics run only here.
  - PLC1 node runs the field-device (Modbus slave on SCADA + IOModules on back ch).
  - MTU node runs the master (reads level over Modbus, decides, writes pump command).
  - pcap is captured on PLC1's SCADA interface => only representative Modbus traffic.

It asserts the real closed loop: connectivity on both LANs, the EPANET app answering
the Integration API, the tank level MOVING under SCADA control (physical evidence,
read back over the Integration API), and Modbus captured in pcap.

REQUIREMENTS (your CORE host, as root; NOT the dev sandbox):
  * CORE 9.x + core-daemon running;
  * the EPANET library importable by the in-node python:
        sudo /opt/core/venv/bin/python -m pip install owa-epanet
    (device/master need only this package; the app needs epanet.)

RUN:
    sudo SCADAVT_DIR="$PWD" /opt/core/venv/bin/python tools/live_core_closed_loop.py

Exit code 0 only if every check PASSED. Nothing is reported as passing unless it
actually ran. A live MITM/integrity attack across CORE is NOT part of this test
(the three-way tamper evidence is covered by the loopback tests); see the report.
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
NODE_PY = os.environ.get("SCADAVT_NODE_PY", "/opt/core/venv/bin/python")
PREFIX = f'cd "{REPO}" && PYTHONPATH="{REPO}"'

try:
    from core.api.grpc import client, wrappers
    from core.api.grpc.wrappers import NodeType, Position
except Exception as e:  # pragma: no cover
    print(f"FATAL: cannot import CORE gRPC API ({e!r}). Run on a CORE host with "
          f"that install's python.", file=sys.stderr)
    sys.exit(3)

from scadavt.core import coregen  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def nrun(core, sid, node, cmd, wait=True):
    return core.node_command(sid, node.id, cmd, wait=wait, shell=True)


def launch(core, sid, node, module_args, logname):
    nrun(core, sid, node,
         f"bash -c '{PREFIX} && setsid {NODE_PY} -m {module_args} "
         f">/tmp/{logname}.log 2>&1 </dev/null &'", wait=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", type=int, default=40)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)

    # the closed-loop scenario descriptor the in-node runners share
    cfg = json.loads((open(os.path.join(REPO, "scadavt", "scenarios",
                                        "closed_loop_min", "lab_speed.json"))).read())
    # pin the addresses this topology uses so session.json carries them: the MTU
    # master reaches the device on the SCADA net (ip:502); the gateway reaches the
    # device's IOModules on the back channel (172.16.0.1:9161)
    cfg["devices"][0]["ip"] = "10.0.0.1"
    cfg["devices"][0]["backchannel"] = "172.16.0.1:9161"
    outdir = tempfile.mkdtemp(prefix="scadavt_cl_")
    coregen.write_session(cfg, outdir)
    os.chmod(outdir, 0o755)
    sess = os.path.join(outdir, "session.json")
    # also hand the runners the full lab config (for the EPANET app + register map)
    labpath = os.path.join(outdir, "lab.json")
    open(labpath, "w").write(json.dumps(cfg))

    core = client.CoreGrpcClient()
    core.connect()
    session = core.create_session()
    sid = session.id

    sw_s = session.add_node(1, name="SW_SCADA", _type=NodeType.SWITCH, position=Position(x=250, y=120))
    sw_m = session.add_node(2, name="SW_MGMT", _type=NodeType.SWITCH, position=Position(x=250, y=360))
    plc = session.add_node(3, name="PLC1", _type=NodeType.DEFAULT, model="PC", position=Position(x=150, y=240))
    mtu = session.add_node(4, name="MTU", _type=NodeType.DEFAULT, model="PC", position=Position(x=400, y=120))
    appn = session.add_node(5, name="APP", _type=NodeType.DEFAULT, model="PC", position=Position(x=400, y=360))

    # SCADA LAN 10.0.0.0/24
    session.add_link(node1=plc, node2=sw_s, iface1=wrappers.Interface(0, ip4="10.0.0.1", ip4_mask=24),
                     iface2=wrappers.Interface(0))
    session.add_link(node1=mtu, node2=sw_s, iface1=wrappers.Interface(0, ip4="10.0.0.254", ip4_mask=24),
                     iface2=wrappers.Interface(1))
    # back-channel LAN 172.16.0.0/24 (out of band)
    session.add_link(node1=plc, node2=sw_m, iface1=wrappers.Interface(1, ip4="172.16.0.1", ip4_mask=24),
                     iface2=wrappers.Interface(0))
    session.add_link(node1=appn, node2=sw_m, iface1=wrappers.Interface(0, ip4="172.16.0.254", ip4_mask=24),
                     iface2=wrappers.Interface(1))
    ok, warn = core.start_session(session)
    check("closed-loop session started", ok, f"warnings={warn}")

    try:
        # connectivity on both planes
        rc, _ = nrun(core, sid, mtu, "ping -c2 -W1 10.0.0.1")
        check("SCADA LAN: MTU -> PLC1 reachable", rc == 0)
        rc, _ = nrun(core, sid, appn, "ping -c2 -W1 172.16.0.1")
        check("back channel: APP -> PLC1 reachable", rc == 0)

        # 1) PLC1 field device: Modbus on SCADA iface, IOModules on back channel
        launch(core, sid, plc, f"scadavt.core.device --session {sess} --node PLC1 "
                               f"--modbus 10.0.0.1:502 --io 0.0.0.0:9161", "PLC1")
        # 2) EPANET app (independent) on the APP node, Integration API on the back channel
        launch(core, sid, appn, f"scadavt.examples.app_runner --config {labpath} "
                                f"--listen 172.16.0.254:9000", "APP")
        time.sleep(4.0)

        # physical baseline read over the Integration API (from the APP node itself)
        rc, out = nrun(core, sid, appn,
                       f"{PREFIX} && {NODE_PY} -c \""
                       f"import asyncio; from scadavt.core import integration as ig;\n"
                       f"c=ig.IntegrationClient('172.16.0.254',9000);\n"
                       f"print(asyncio.run(c.acquire('T1_LEV')).value)\"")
        try:
            level0 = float(out.strip().splitlines()[-1])
        except Exception:
            level0 = None
        check("EPANET app answers Integration API (ACQUIRE level)", level0 is not None,
              f"level0={level0}")

        # 3) gateway (APP node): app <-> PLC registers + drive EPANET's clock
        launch(core, sid, appn, f"scadavt.core.gateway_run --session {sess} "
                                f"--app 172.16.0.254:9000 --step 120 --cycles {a.cycles} --sleep 0.1", "GW")
        # 4) MTU master (SCADA net): read level, decide pump speed, write command
        launch(core, sid, mtu, f"scadavt.core.master --session {sess} --node MTU "
                               f"--step 0.1 --cycles {a.cycles}", "MTU")

        # 5) capture Modbus on PLC1's SCADA interface while the loop runs
        nrun(core, sid, plc, "bash -c 'setsid tcpdump -i eth0 -w /tmp/cl.pcap tcp port 502 "
                             ">/tmp/td.log 2>&1 </dev/null &'", wait=False)
        time.sleep(max(8.0, a.cycles * 0.25))        # let the closed loop run

        # physical evidence: tank level moved under SCADA control
        rc, out = nrun(core, sid, appn,
                       f"{PREFIX} && {NODE_PY} -c \""
                       f"import asyncio; from scadavt.core import integration as ig;\n"
                       f"c=ig.IntegrationClient('172.16.0.254',9000);\n"
                       f"print(asyncio.run(c.acquire('T1_LEV')).value)\"")
        try:
            level1 = float(out.strip().splitlines()[-1])
        except Exception:
            level1 = None
        moved = (level0 is not None and level1 is not None and abs(level1 - level0) > 0.3)
        check("tank level changed under SCADA control (physical evidence)", moved,
              f"level {level0} -> {level1}")

        # stop capture, confirm Modbus packets
        nrun(core, sid, plc, "pkill -f 'tcpdump -i eth0' || true")
        time.sleep(0.5)
        rc, out = nrun(core, sid, plc, "tcpdump -r /tmp/cl.pcap 2>/dev/null | wc -l")
        n = int((out or "0").strip().split()[-1]) if out.strip() else 0
        check("Modbus/TCP captured in pcap on the SCADA network", n > 0, f"{n} packets")
    finally:
        if not a.keep:
            try:
                core.delete_session(sid)
            except Exception:
                pass

    print("\n" + "=" * 60 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    print("  logs in each node: /tmp/PLC1.log /tmp/APP.log /tmp/GW.log /tmp/MTU.log")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
