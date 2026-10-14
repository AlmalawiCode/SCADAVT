#!/usr/bin/env python3
"""Live end-to-end integration test for SCADAVT on real CORE 9.x namespaces.

This is NOT a static test. It stands up actual CORE nodes (Linux network
namespaces), launches the real SCADAVT device/master runners inside them, and
asserts real behaviour: node-to-node connectivity, Modbus sensor reads, control
decisions, actuator writes, PCAP capture, routing across a WAN, and the measured
effect of netem delay/loss.

REQUIREMENTS (your Ubuntu machine, not the dev sandbox):
  * CORE 9.x installed and `core-daemon` running as root (gRPC on :50051);
  * this repo present; its device/master runners only need stdlib + this package.

RUN (as root, from the repo root):
    sudo SCADAVT_DIR="$PWD" /opt/core/venv/bin/python tools/live_core_integration.py
  (The orchestrator needs CORE's `core` package — hence the CORE venv python.
   The in-node runners use the system `python3` with PYTHONPATH=$SCADAVT_DIR.)

Options:   --lan-only | --wan-only | --keep (don't delete the session on exit)

Exit code is 0 only if every check PASSED. Nothing is reported as passing unless
it actually ran and the assertion held.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# in-node runners use the system python3 + this repo on PYTHONPATH
NODE_PY = os.environ.get("SCADAVT_NODE_PY", "python3")
PREFIX = f'cd "{REPO}" && PYTHONPATH="{REPO}"'

try:
    from core.api.grpc import client, wrappers
    from core.api.grpc.wrappers import NodeType, Position, LinkOptions
except Exception as e:  # pragma: no cover - only on a non-CORE host
    print(f"FATAL: cannot import CORE's gRPC API ({e!r}).\n"
          f"Run this on a CORE host with CORE installed, using that install's "
          f"python (e.g. /opt/core/venv/bin/python).", file=sys.stderr)
    sys.exit(3)

from scadavt.core import coregen  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def hr(t: str) -> None:
    print("\n" + "=" * 70 + f"\n  {t}\n" + "=" * 70)


# --------------------------------------------------------------- node commands
def node_run(core, sid, node, command, wait=True):
    """Run a shell command inside a node namespace; return (rc, output)."""
    return core.node_command(sid, node.id, command, wait=wait, shell=True)


def launch(core, sid, node, module_args, logname):
    """Launch a long-running SCADAVT runner inside a node, fully detached.

    Uses wait=False and setsid + closed stdin so node_command returns at once
    (otherwise CORE waits on the still-running server and the test hangs)."""
    cmd = (f"bash -c '{PREFIX} && setsid {NODE_PY} -m {module_args} "
           f">/tmp/{logname}.log 2>&1 </dev/null &'")
    return node_run(core, sid, node, cmd, wait=False)


def ping(core, sid, node, target_ip, count=3):
    rc, out = node_run(core, sid, node, f"ping -c{count} -W1 {target_ip}")
    return rc, out


def parse_rtt_avg(ping_out: str):
    # "rtt min/avg/max/mdev = 0.1/0.2/0.3/0.0 ms"
    for line in ping_out.splitlines():
        if "min/avg/max" in line and "=" in line:
            try:
                return float(line.split("=")[1].strip().split("/")[1])
            except Exception:
                return None
    return None


def parse_loss_pct(ping_out: str):
    for line in ping_out.splitlines():
        if "packet loss" in line:
            try:
                return float(line.split("%")[0].split()[-1])
            except Exception:
                return None
    return None


def write_descriptor(cfg, name):
    # a temp dir (not the repo) so there are no permission/ownership clashes;
    # nodes share the filesystem, so the in-node runners read it by absolute path
    out = tempfile.mkdtemp(prefix=f"scadavt_it_{name}_")
    coregen.write_session(cfg, out)
    os.chmod(out, 0o755)
    return out


# ---------------------------------------------------------------------- LAN
def test_lan(core):
    hr("TEST 1 — LAN: one PLC + one MTU on a switch (10.0.0.0/24)")
    cfg = {
        "name": "it-lan", "control": {"placement": "mtu"}, "netmask": 24,
        "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC1", "ip": "10.0.0.1", "backchannel": "127.0.0.1:9161",
                     "points": [{"id": "lvl", "role": "measurement", "is_bool": False},
                                {"id": "pmp", "role": "command", "is_bool": True}]}],
        "mapping": [{"point": "lvl", "device": "PLC1", "role": "measurement", "element": "T1"},
                    {"point": "pmp", "device": "PLC1", "role": "command", "element": "P1"}],
        "rules": [{"id": "on", "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                                       "op": "below", "value": 3.0}],
                   "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "open"}]}],
    }
    assert coregen.validate(cfg) == [], coregen.validate(cfg)
    outdir = write_descriptor(cfg, "lan")
    sess_path = os.path.join(outdir, "session.json")

    session = core.create_session()
    sid = session.id
    sw = session.add_node(1, name="SW1", _type=NodeType.SWITCH, position=Position(x=300, y=150))
    plc = session.add_node(2, name="PLC1", _type=NodeType.DEFAULT, model="PC",
                           position=Position(x=200, y=300))
    mtu = session.add_node(3, name="MTU", _type=NodeType.DEFAULT, model="PC",
                           position=Position(x=400, y=300))
    session.add_link(node1=plc, node2=sw,
                     iface1=wrappers.Interface(0, ip4="10.0.0.1", ip4_mask=24),
                     iface2=wrappers.Interface(0))
    session.add_link(node1=mtu, node2=sw,
                     iface1=wrappers.Interface(0, ip4="10.0.0.254", ip4_mask=24),
                     iface2=wrappers.Interface(1))
    ok, warnings = core.start_session(session)
    check("LAN session started", ok, f"warnings={warnings}")

    try:
        # 1) connectivity: MTU -> PLC over the switch
        rc, out = ping(core, sid, mtu, "10.0.0.1")
        check("MTU can reach PLC1 (ping over LAN)", rc == 0, out.strip().splitlines()[-1] if out else "")

        # 2) start the PLC device runner IN its namespace, seeded with a low level
        launch(core, sid, plc,
               f"scadavt.core.device --session {sess_path} --node PLC1 "
               f"--modbus 10.0.0.1:502 --io 0.0.0.0:9161 --seed lvl=2.0", "PLC1")
        time.sleep(3.0)                                   # let the servers bind

        # 3) sensor read: from the MTU node, read PLC1.lvl over Modbus/TCP
        rc, out = node_run(core, sid, mtu,
                           f"{PREFIX} && {NODE_PY} -m scadavt.core.probe "
                           f"--session {sess_path} --node PLC1 --point lvl")
        got_level = ("lvl = 2.0" in out) or ("lvl = 1.9" in out) or ("lvl = 2.00" in out)
        check("MTU reads PLC1 sensor register over Modbus (~2.0)", rc == 0 and got_level, out.strip())

        # 4) start a PCAP capture on the PLC interface, then drive control traffic
        node_run(core, sid, plc, "bash -c 'setsid tcpdump -i eth0 -w /tmp/plc1.pcap tcp port 502 "
                                  ">/tmp/tcpdump.log 2>&1 </dev/null &'", wait=False)
        time.sleep(1.5)

        # 5) control decision + actuator write: run the MTU master a few cycles
        rc, out = node_run(core, sid, mtu,
                           f"{PREFIX} && {NODE_PY} -m scadavt.core.master "
                           f"--session {sess_path} --node MTU --step 0.2 --cycles 5")
        check("MTU master ran the control loop over Modbus", rc == 0, out.strip()[-200:])

        # 6) actuator write confirmed: read the pump command register back
        rc, out = node_run(core, sid, mtu,
                           f"{PREFIX} && {NODE_PY} -m scadavt.core.probe "
                           f"--session {sess_path} --node PLC1 --point pmp")
        check("Rule fired: PLC1 pump command register = 1 (level<3 -> open)",
              rc == 0 and "pmp = 1" in out, out.strip())

        # 7) stop capture, confirm Modbus packets were captured
        node_run(core, sid, plc, "pkill -f 'tcpdump -i eth0' || true", wait=True)
        time.sleep(0.5)
        rc, out = node_run(core, sid, plc, "tcpdump -r /tmp/plc1.pcap 2>/dev/null | wc -l")
        n = int((out or "0").strip().split()[-1]) if out.strip() else 0
        check("PCAP captured Modbus/TCP packets on the PLC node", n > 0, f"{n} packets in /tmp/plc1.pcap")
    finally:
        pass
    return sid


# ---------------------------------------------------------------------- WAN
def test_wan(core):
    hr("TEST 2 — WAN: PLCs on separate subnets via a router, with netem delay/loss")
    DELAY_MS, LOSS_PCT = 50, 10.0
    cfg = {
        "name": "it-wan", "control": {"placement": "mtu"}, "netmask": 24,
        "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC1", "ip": "10.0.0.1",
                     "points": [{"id": "lvl", "role": "measurement", "is_bool": False}]},
                    {"id": "PLC2", "ip": "10.0.1.1",
                     "points": [{"id": "lvl", "role": "measurement", "is_bool": False}]}],
        "switches": [{"id": "SW_A"}, {"id": "SW_B"}], "routers": [{"id": "R1"}],
        "links": [{"a": "PLC1", "b": "SW_A"}, {"a": "MTU", "b": "SW_A"},
                  {"a": "PLC2", "b": "SW_B"},
                  {"a": "R1", "b": "SW_A", "a_ip": "10.0.0.250"},
                  {"a": "R1", "b": "SW_B", "a_ip": "10.0.1.250",
                   "delay_ms": DELAY_MS, "loss": LOSS_PCT}],
        "mapping": [{"point": "lvl", "device": "PLC2", "role": "measurement", "element": "T2"}],
    }
    assert coregen.validate(cfg) == [], coregen.validate(cfg)
    outdir = write_descriptor(cfg, "wan")
    sess_path = os.path.join(outdir, "session.json")

    session = core.create_session()
    sid = session.id
    swa = session.add_node(1, name="SW_A", _type=NodeType.SWITCH, position=Position(x=200, y=150))
    swb = session.add_node(2, name="SW_B", _type=NodeType.SWITCH, position=Position(x=600, y=150))
    r1 = session.add_node(3, name="R1", _type=NodeType.DEFAULT, model="router",
                          position=Position(x=400, y=150))
    plc1 = session.add_node(4, name="PLC1", _type=NodeType.DEFAULT, model="PC",
                            position=Position(x=120, y=300))
    mtu = session.add_node(5, name="MTU", _type=NodeType.DEFAULT, model="PC",
                           position=Position(x=280, y=300))
    plc2 = session.add_node(6, name="PLC2", _type=NodeType.DEFAULT, model="PC",
                            position=Position(x=600, y=300))
    # LAN A (10.0.0.0/24)
    session.add_link(node1=plc1, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.0.1", ip4_mask=24),
                     iface2=wrappers.Interface(0))
    session.add_link(node1=mtu, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.0.254", ip4_mask=24),
                     iface2=wrappers.Interface(1))
    # LAN B (10.0.1.0/24)
    session.add_link(node1=plc2, node2=swb, iface1=wrappers.Interface(0, ip4="10.0.1.1", ip4_mask=24),
                     iface2=wrappers.Interface(0))
    # router legs (one per subnet); impairment on the B-side leg = WAN
    session.add_link(node1=r1, node2=swa, iface1=wrappers.Interface(0, ip4="10.0.0.250", ip4_mask=24),
                     iface2=wrappers.Interface(2))
    session.add_link(node1=r1, node2=swb, iface1=wrappers.Interface(1, ip4="10.0.1.250", ip4_mask=24),
                     iface2=wrappers.Interface(1),
                     options=LinkOptions(delay=DELAY_MS * 1000, loss=LOSS_PCT))
    ok, warnings = core.start_session(session)
    check("WAN session started", ok, f"warnings={warnings}")

    # Bring up L3 routing deterministically (don't depend on OSPF convergence or
    # on which routing services the model attached): enable IP forwarding on the
    # router and give each endpoint a default route to the router's leg in its
    # own subnet. The router is directly connected to both subnets, so this is
    # all that is needed for MTU (10.0.0.0/24) to reach PLC2 (10.0.1.0/24).
    print("   (configuring routing: IP forwarding on R1 + default routes)")
    node_run(core, sid, r1, "sysctl -w net.ipv4.ip_forward=1")
    node_run(core, sid, plc1, "ip route replace default via 10.0.0.250")
    node_run(core, sid, mtu, "ip route replace default via 10.0.0.250")
    node_run(core, sid, plc2, "ip route replace default via 10.0.1.250")
    time.sleep(2.0)

    try:
        # sanity: each endpoint can reach its own router leg (same subnet)
        rc_g, _ = ping(core, sid, mtu, "10.0.0.250", count=2)
        check("MTU reaches the router (gateway on its LAN)", rc_g == 0)

        # 1) routing: MTU in 10.0.0.0/24 reaches PLC2 in 10.0.1.0/24 across the router
        rc, out = ping(core, sid, mtu, "10.0.1.1", count=5)
        check("Routing works: MTU -> PLC2 across the router (different subnets)",
              rc == 0, (out.strip().splitlines()[-1] if out else ""))

        # 2) configurable delay takes effect: RTT reflects the netem delay
        rtt = parse_rtt_avg(out or "")
        check(f"netem delay applied: avg RTT > {DELAY_MS}ms (one-way {DELAY_MS}ms set)",
              rtt is not None and rtt > DELAY_MS, f"avg RTT = {rtt} ms")

        # 3) configurable loss takes effect: measurable packet loss across the WAN link
        rc, out = node_run(core, sid, mtu, "ping -c50 -W1 10.0.1.1")
        loss = parse_loss_pct(out or "")
        check(f"netem loss applied: measurable packet loss (>0%, set {LOSS_PCT}%)",
              loss is not None and loss > 0.0, f"measured loss = {loss}%")

        # 4) real Modbus communication survives across the WAN
        launch(core, sid, plc2,
               f"scadavt.core.device --session {sess_path} --node PLC2 "
               f"--modbus 10.0.1.1:502 --io 0.0.0.0:9161 --seed lvl=5.5", "PLC2")
        time.sleep(3.0)
        rc, out = node_run(core, sid, mtu,
                           f"{PREFIX} && {NODE_PY} -m scadavt.core.probe "
                           f"--session {sess_path} --node PLC2 --point lvl --timeout 5")
        check("MTU reads PLC2 sensor over Modbus across the WAN (~5.5)",
              rc == 0 and "lvl = 5.5" in out, out.strip())
    finally:
        pass
    return sid


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lan-only", action="store_true")
    ap.add_argument("--wan-only", action="store_true")
    ap.add_argument("--keep", action="store_true", help="do not delete the sessions on exit")
    a = ap.parse_args(argv)

    core = client.CoreGrpcClient()
    core.connect()
    sids = []
    try:
        if not a.wan_only:
            sids.append(test_lan(core))
        if not a.lan_only:
            sids.append(test_wan(core))
    finally:
        if not a.keep:
            for sid in sids:
                try:
                    core.delete_session(sid)
                except Exception as e:
                    print(f"   (cleanup: could not delete session {sid}: {e})")

    hr("RESULT")
    print(f"  PASSED: {len(PASS)}    FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    ready = not FAIL
    print(f"\n  {'ALL CHECKS PASSED — the live CORE path works.' if ready else 'FAILURES PRESENT — not ready.'}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
