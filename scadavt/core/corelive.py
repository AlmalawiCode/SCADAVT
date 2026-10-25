"""Generic live CORE driver — the framework's Python API for standing up an
emulated SCADA network of *arbitrary size* programmatically.

Give it a domain-neutral :mod:`scadavt.core.coregen` config (any number of
PLC/field devices, an MTU, switches and routers, and the links between them) and
it builds and runs the whole thing on a live CORE emulator:

* every **field device** (PLC) becomes a real CORE Linux-namespace node with an
  IP on its interface, running the actual PLC device program *inside* the node;
* the **MTU** becomes a node running the control master;
* a **switch** node is one LAN segment (its members reach each other directly);
* a **router** node joins subnets into a WAN (IP forwarding + default routes are
  brought up automatically from the config's addressing);
* per-link impairment (delay / loss / bandwidth) is real ``netem``.

There is **no per-scenario code**: the same driver stands up 1 PLC or 100. A
user writes a plain Python script that assembles a config dict (or builds one
with :func:`grid`/:func:`chain` helpers) and calls::

    from scadavt.core import corelive
    net = corelive.build(core, cfg)     # create session + nodes + links
    net.start()                         # start CORE, routing, launch programs
    net.ping("MTU", plc_ip)             # drive / inspect it
    val = net.probe("MTU", "PLC7", "a") # read a point across the network
    net.stop()

The topology maths (:func:`build_plan`) is pure and CORE-independent, so it is
unit-tested offline; only :func:`build`/:meth:`LiveNetwork.start` need a daemon.
"""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import time

from . import coregen
from . import fieldbus as fb

NODE_PY = os.environ.get("SCADAVT_NODE_PY", "/opt/core/venv/bin/python")
REPO = os.environ.get(
    "SCADAVT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
PREFIX = f'cd "{REPO}" && PYTHONPATH="{REPO}"'


# --------------------------------------------------------------------------
# pure topology plan (no CORE) — testable offline
# --------------------------------------------------------------------------
def build_plan(cfg: dict) -> dict:
    """Resolve a config into the exact ordered node/link build plan for CORE.

    Returns a dict with:

    ``nodes``    list of ``{nid, id, kind, type, model, pos}`` in build order
                 (``type`` is the CORE NodeType name: SWITCH / HUB / DEFAULT).
    ``links``    list of ``{a, b, a_iface, b_iface, opts}`` where each iface is
                 ``{idx, ip, mask}`` (ip is None for a switch/hub end).
    ``routers``  router node ids (need IP forwarding).
    ``gateways`` ``{endpoint_id: gateway_ip}`` default routes for a WAN.

    Interface indices are assigned per node in link order — the pattern CORE's
    own gRPC build uses — so this matches :func:`coregen.core_build_script`.
    """
    s = coregen.session(cfg)
    mask = s["netmask"]
    by_id = {n["id"]: n for n in s["nodes"]}

    nodes = []
    for i, n in enumerate(s["nodes"], 1):
        if n["kind"] in ("switch", "hub"):
            ntype = "SWITCH" if n["kind"] == "switch" else "HUB"
            model = None
        else:
            ntype = "DEFAULT"
            model = n.get("model") or ("router" if n["kind"] == "router" else "PC")
        nodes.append({
            "nid": i, "id": n["id"], "kind": n["kind"], "type": ntype,
            "model": model,
            "pos": (120 + (i % 6) * 150, 160 + (i // 6) * 140),
        })

    ifc: dict = {}

    def iface(node_id: str, lk: dict, side: str) -> dict:
        idx = ifc.get(node_id, 0)
        ifc[node_id] = idx + 1
        ip = coregen._node_ip(by_id[node_id], lk, side)
        return {"idx": idx, "ip": ip, "mask": mask}

    links = []
    for lk in s["links"]:
        a, b = lk["a"], lk["b"]
        opts = {}
        if lk.get("delay_ms"):
            opts["delay"] = int(lk["delay_ms"]) * 1000       # ms -> microseconds
        if lk.get("loss"):
            opts["loss"] = float(lk["loss"])
        if lk.get("bw"):
            opts["bandwidth"] = int(lk["bw"])
        links.append({"a": a, "b": b,
                      "a_iface": iface(a, lk, "a"),
                      "b_iface": iface(b, lk, "b"),
                      "opts": opts})

    routers, gateways = coregen.routing_plan(s, mask)
    return {"nodes": nodes, "links": links, "routers": routers, "gateways": gateways}


# --------------------------------------------------------------------------
# config helpers — assemble a large network without writing every link by hand
# --------------------------------------------------------------------------
def _device(plc_id: str, ip: str, seed: float | None = None) -> dict:
    """A register-native PLC: an input register ``s`` (the value the MTU reads)
    and a coil ``a`` (the output the MTU drives). No sensor/actuator semantics —
    just Modbus registers."""
    d = {"id": plc_id, "ip": ip, "protocol": "modbus",
         "registers": [
             {"id": "s", "area": "input_register", "data_type": "float32",
              "initial": seed if seed is not None else 0.0},
             {"id": "a", "area": "coil", "data_type": "bool", "initial": False}]}
    if seed is not None:
        d["seed"] = seed                     # also seeded at launch via --seed s=
    return d


def grid(n_plcs: int, per_subnet: int = 8, base: str = "10.0.{seg}.0",
         mtu_subnet: int = 1, seed_low=2.0, seed_high=5.0, name="bignet") -> dict:
    """Build a config for ``n_plcs`` PLCs spread over as many /24 subnets as
    needed (``per_subnet`` each), every subnet joined by one central router (a
    WAN), with one MTU and a cross-PLC control ring.

    PLC *k* sensor low -> opens PLC *k+1* actuator (ring), so every actuator is
    commanded from *another* PLC's reading, across subnet boundaries. Sensors
    alternate low/high so the expected actuator pattern is deterministic.
    """
    import math
    n_sub = max(1, math.ceil(n_plcs / per_subnet))
    devices, links, switches = [], [], []
    routers = [{"id": "R1", "model": "router"}]
    # one switch per subnet; router leg .250 on each
    plc_ips: dict[str, str] = {}
    seg_of: dict[str, int] = {}
    k = 0
    for seg in range(1, n_sub + 1):
        sw = f"SW{seg}"
        switches.append({"id": sw})
        net = base.format(seg=seg)
        net3 = net.rsplit(".", 1)[0]
        # router leg on this subnet
        links.append({"a": "R1", "b": sw, "a_ip": f"{net3}.250"})
        host = 1
        while k < n_plcs and host <= per_subnet:
            k += 1
            pid = f"PLC{k}"
            ip = f"{net3}.{host}"
            seed = seed_low if k % 2 == 1 else seed_high
            devices.append(_device(pid, ip, seed))
            links.append({"a": pid, "b": sw, "a_ip": ip})
            plc_ips[pid] = ip
            seg_of[pid] = seg
            host += 1
    # MTU on subnet `mtu_subnet`
    mnet3 = base.format(seg=mtu_subnet).rsplit(".", 1)[0]
    mtu = {"id": "MTU", "ip": f"{mnet3}.254"}
    links.append({"a": "MTU", "b": f"SW{mtu_subnet}", "a_ip": mtu["ip"]})

    # cross-PLC control ring, register-based: PLC i's input register 's' low ->
    # set PLC i+1's coil 'a'. Each actuator is driven from another PLC's reading,
    # over Modbus, by the central MTU.
    logic = []
    for i in range(1, n_plcs + 1):
        tgt = i % n_plcs + 1                     # ring: PLC i -> PLC i+1 (wrap)
        logic.append({"id": f"r{i}",
                      "if": [{"device": f"PLC{i}", "register": "s", "op": "below", "value": 3.0}],
                      "then": [{"device": f"PLC{tgt}", "register": "a", "set": 1}]})
    return {"name": name, "control": {"placement": "mtu"},
            "mtu": mtu, "devices": devices, "switches": switches,
            "routers": routers, "links": links, "logic": logic,
            "_plc_ips": plc_ips, "_seg_of": seg_of}


# --------------------------------------------------------------------------
# live driver
# --------------------------------------------------------------------------
class LiveNetwork:
    """A built (and optionally started) CORE network. Thin, generic handles."""

    def __init__(self, core, session, sid, cfg, plan, workdir, handles):
        self.core = core
        self.session = session
        self.sid = sid
        self.cfg = cfg
        self.plan = plan
        self.workdir = workdir
        self.handles = handles                   # {node_id: core node wrapper}
        self.sess_json = os.path.join(workdir, "session.json")

    # -- raw command in a node's namespace ---------------------------------
    def nrun(self, node_id, cmd, wait=True):
        return self.core.node_command(self.sid, self.handles[node_id].id, cmd,
                                      wait=wait, shell=True)

    def _launch(self, node_id, module_args, logname):
        self.nrun(node_id,
                  f"bash -c '{PREFIX} && setsid {NODE_PY} -m {module_args} "
                  f">/tmp/{logname}.log 2>&1 </dev/null &'", wait=False)

    # -- bring the whole network to life -----------------------------------
    def start(self, settle: float = 3.0, cycles: int = 60):
        # give device bring-up more time as the network grows (each field node
        # spawns its own process); the master also retries over its cycles.
        settle = max(settle, 0.3 * len(self.cfg["devices"]))
        ok, warn = self.core.start_session(self.session)
        # WAN bring-up: IP forwarding on routers, default route on each endpoint
        for rid in self.plan["routers"]:
            self.nrun(rid, "sysctl -w net.ipv4.ip_forward=1")
        for nid, gw in self.plan["gateways"].items():
            self.nrun(nid, f"ip route replace default via {gw}")
        if self.plan["routers"]:
            time.sleep(1.0)
        # launch each field device (seeding its sensor when the config asks)
        for d in self.cfg["devices"]:
            ip = d.get("ip", "0.0.0.0")
            port = fb.PROTOCOLS[d.get("protocol", "modbus")]["default_port"]
            seed = f" --seed s={d['seed']}" if d.get("seed") is not None else ""
            self._launch(d["id"],
                         f"scadavt.core.device --session {self.sess_json} --node {d['id']} "
                         f"--modbus {ip}:{port} --io 0.0.0.0:9161{seed}", d["id"])
        time.sleep(settle)
        # launch the MTU master (drives all devices via the cross-PLC rules)
        if self.cfg.get("mtu"):
            self._launch(self.cfg["mtu"]["id"],
                         f"scadavt.core.master --session {self.sess_json} "
                         f"--node {self.cfg['mtu']['id']} --step 0.1 --cycles {cycles}",
                         self.cfg["mtu"]["id"])
        return ok, warn

    # -- inspection primitives (what a user script calls) ------------------
    def ping(self, src_node, dst_ip, count=2):
        rc, _ = self.nrun(src_node, f"ping -c{count} -W1 {dst_ip}")
        return rc == 0

    def probe(self, from_node, target_node, point, write=None, timeout=5):
        w = f" --write {write}" if write is not None else ""
        _, out = self.nrun(from_node,
                           f"{PREFIX} && {NODE_PY} -m scadavt.core.probe "
                           f"--session {self.sess_json} --node {target_node} "
                           f"--point {point} --timeout {timeout}{w}")
        if write is not None:
            return out
        try:
            return float((out or "").strip().split("=")[-1].split("@")[0])
        except Exception:
            return None

    def capture_start(self, node_id, pcap_path, expr="tcp port 502", bind_wait=2.0):
        # -U = packet-buffered (flush each packet) so a short capture under heavy
        # host load does not lose frames to libpcap's buffer.
        self.nrun(node_id, f"bash -c 'rm -f {pcap_path}; setsid tcpdump -i eth0 -U "
                           f"-w {pcap_path} {expr} >/tmp/td.log 2>&1 </dev/null &'", wait=False)
        time.sleep(bind_wait)                        # let tcpdump actually start capturing

    def capture_stop(self, node_id, drain=1.5):
        # let -U writes land, then ask tcpdump to flush + exit cleanly (SIGINT),
        # not a hard kill that can race the final write under heavy host load.
        time.sleep(drain)
        self.nrun(node_id, "pkill -INT -f 'tcpdump -i eth0' || true")
        time.sleep(0.8)

    def capture(self, node_id, pcap_path, expr="tcp port 502", seconds=5):
        self.capture_start(node_id, pcap_path, expr)
        time.sleep(seconds)
        self.capture_stop(node_id)

    def talked_to(self, node_id, pcap_path, ip):
        _, o = self.nrun(node_id, f"tcpdump -r {pcap_path} 'host {ip}' 2>/dev/null | wc -l")
        try:
            return int((o or "0").strip().split()[-1]) > 0
        except Exception:
            return False

    def stop(self):
        try:
            self.core.delete_session(self.sid)
        except Exception:
            pass


def build(core, cfg: dict, workdir: str | None = None) -> LiveNetwork:
    """Validate ``cfg``, create a CORE session, and add every node and link from
    the plan. Does not start the session — call :meth:`LiveNetwork.start`."""
    issues = coregen.validate(cfg)
    if issues:
        raise coregen.ConfigError("; ".join(issues))
    plan = build_plan(cfg)

    workdir = workdir or tempfile.mkdtemp(prefix="scadavt_net_")
    coregen.write_session(cfg, workdir)
    os.chmod(workdir, 0o755)

    from core.api.grpc import wrappers
    from core.api.grpc.wrappers import NodeType, Position

    session = core.create_session()
    sid = session.id
    # if building the topology fails partway, don't leak the daemon session
    try:
        handles: dict = {}
        for n in plan["nodes"]:
            ntype = getattr(NodeType, n["type"])
            pos = Position(x=n["pos"][0], y=n["pos"][1])
            if n["model"] is None:
                handles[n["id"]] = session.add_node(n["nid"], name=n["id"], _type=ntype, position=pos)
            else:
                handles[n["id"]] = session.add_node(n["nid"], name=n["id"], _type=ntype,
                                                    model=n["model"], position=pos)
        for lk in plan["links"]:
            def mk(side, lk=lk):
                f = lk[f"{side}_iface"]
                if f["ip"] is None:
                    return wrappers.Interface(f["idx"])
                return wrappers.Interface(f["idx"], ip4=f["ip"], ip4_mask=f["mask"])
            opts = wrappers.LinkOptions(**lk["opts"]) if lk["opts"] else None
            kw = {"options": opts} if opts else {}
            session.add_link(node1=handles[lk["a"]], node2=handles[lk["b"]],
                             iface1=mk("a"), iface2=mk("b"), **kw)
    except Exception:
        try:
            core.delete_session(sid)
        except Exception:
            pass
        raise
    return LiveNetwork(core, session, sid, cfg, plan, workdir, handles)
