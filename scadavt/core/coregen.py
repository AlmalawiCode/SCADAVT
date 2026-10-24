"""Minimal, configuration-driven CORE session generator (proposal V6, §8 M1).

Turns a domain-neutral SCADAVT configuration into the artefacts needed to stand
up a run:

* a canonical ``session`` descriptor (nodes, services, links, mappings) that is
  validated structurally;
* a CORE build script (``core.api.grpc`` style) that, on a host where CORE runs
  with privileges, creates the nodes, assigns the field-bus / I-O services and
  applies per-link delay and loss (``netem``);
* the two IOModuleGate configuration files (endpoints + point map), generalised
  from the thesis's Table 3.1.

It makes **no domain assumptions**: it only uses the ids and fields given in the
configuration, so the same generator serves water, power or any other setup.
CORE itself is not required to generate or validate these artefacts, which is
why multi-configuration validation (M1) does not depend on a running emulator.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path

from ..util import hostport
from . import fieldbus as fb
from . import integration as ig


class ConfigError(ValueError):
    pass


def _validate_device_data(d: dict) -> list[str]:
    """Validate a device's data model: native registers, or (transitionally)
    legacy role-points — never both (one model only)."""
    out: list[str] = []
    did = d.get("id")
    has_regs, has_pts = bool(d.get("registers")), bool(d.get("points"))
    if has_regs and has_pts:
        out.append(f"{did}: declares both 'registers' and legacy 'points' — use one model")
    seen: dict = {}
    for r in d.get("registers", []):
        rid, area = r.get("id"), r.get("area")
        if area not in fb.AREAS:
            out.append(f"{did}.{rid}: bad register area {area!r} "
                       f"(one of {', '.join(fb.AREAS)})")
            continue
        dt = r.get("data_type", "float32")
        if dt not in fb.DATA_TYPES:
            out.append(f"{did}.{rid}: bad data_type {dt!r}")
        addr = r.get("address")
        if addr is not None and (not isinstance(addr, int) or addr < 0):
            out.append(f"{did}.{rid}: bad address {addr!r}")
        if "address" in r:                       # only pinned addresses can clash
            width = 1 if dt == "bool" else fb._DTYPE_WORDS.get(dt, 2)
            for off in range(width):
                key = (area, int(addr or 0) + off)
                if key in seen:
                    out.append(f"{did}.{rid}: {area} address {key[1]} overlaps {seen[key]!r}")
                seen[key] = rid
    for p in d.get("points", []):
        if p.get("role") not in ig.ROLES:
            out.append(f"{did}.{p.get('id')}: bad role {p.get('role')!r}")
    return out


def validate(cfg: dict) -> list[str]:
    """Return a list of problems; empty means the configuration is usable."""
    issues: list[str] = []
    devices = cfg.get("devices", [])
    if not devices:
        issues.append("no devices defined")
    ids = [d.get("id") for d in devices]
    if len(set(ids)) != len(ids):
        issues.append("duplicate device ids")
    for d in devices:
        proto = d.get("protocol", "modbus")
        if proto not in fb.PROTOCOLS:
            issues.append(f"{d.get('id')}: unknown field-bus protocol {proto!r}")
        issues += _validate_device_data(d)
    # control placement
    placement = cfg.get("control", {}).get("placement", "mtu")
    if placement not in ("mtu", "plc", "distributed"):
        issues.append(f"bad control placement {placement!r}")
    if placement == "mtu" and not cfg.get("mtu"):
        issues.append("centralized (mtu) placement requires an 'mtu' node")
    # mapping references real devices/points (registers or legacy points)
    known = {(d["id"], p["id"]) for d in devices for p in d.get("points", [])}
    known |= {(d["id"], r["id"]) for d in devices for r in d.get("registers", [])}
    for m in cfg.get("mapping", []):
        if (m.get("device"), m.get("point")) not in known:
            issues.append(f"mapping references unknown point {m.get('device')}.{m.get('point')}")
    # register-based control logic references real (device, register) points, and
    # a 'then' target must be a writable register (coil/holding — not read-only).
    writable = {(d["id"], r["id"]) for d in devices for r in d.get("registers", [])
                if r.get("area") in ("coil", "holding_register")}
    for rule in cfg.get("logic", []):
        for c in rule.get("if", []):
            if (c.get("device"), c.get("register")) not in known:
                issues.append(f"logic {rule.get('id')}: unknown register "
                              f"{c.get('device')}.{c.get('register')}")
        for a in rule.get("then", []):
            ref = (a.get("device"), a.get("register"))
            if ref not in known:
                issues.append(f"logic {rule.get('id')}: unknown register {ref[0]}.{ref[1]}")
            elif writable and ref not in writable:
                issues.append(f"logic {rule.get('id')}: target {ref[0]}.{ref[1]} is a "
                              f"read-only register (use a coil/holding_register)")
    # addressing uniqueness (IPs and back channels must not clash)
    ips = [d.get("ip") for d in devices if d.get("ip")]
    if cfg.get("mtu", {}).get("ip"):
        ips.append(cfg["mtu"]["ip"])
    if len(set(ips)) != len(ips):
        issues.append("duplicate node IP addresses")
    bcs = [d.get("backchannel") for d in devices if d.get("backchannel")]
    if len(set(bcs)) != len(bcs):
        issues.append("duplicate back-channel addresses")
    # links must reference known endpoints (devices, mtu, routers or switches)
    endpoints = set(ids) | {cfg.get("mtu", {}).get("id")} \
        | {s.get("id") for s in cfg.get("switches", [])} \
        | {r.get("id") for r in cfg.get("routers", [])}
    net_nodes = {s.get("id") for s in cfg.get("switches", [])}
    routers = {r.get("id") for r in cfg.get("routers", [])}
    ip_of = {d["id"]: d.get("ip") for d in devices}
    if cfg.get("mtu"):
        ip_of[cfg["mtu"]["id"]] = cfg["mtu"].get("ip")
    for lk in cfg.get("links", []):
        for end, side in ((lk.get("a"), "a"), (lk.get("b"), "b")):
            if end not in endpoints:
                issues.append(f"link references unknown endpoint {end!r}")
                continue
            # an addressable endpoint needs an IP: from the node, or per-link for a router
            if end in net_nodes:
                continue
            if lk.get(f"{side}_ip") or ip_of.get(end):
                continue
            if end in routers:
                issues.append(f"router {end!r} needs an IP on link to "
                              f"{lk.get('b' if side == 'a' else 'a')!r} ({side}_ip)")
    return issues


def session(cfg: dict) -> dict:
    """Build the canonical session descriptor (validated)."""
    issues = validate(cfg)
    if issues:
        raise ConfigError("; ".join(issues))
    nodes = []
    for d in cfg["devices"]:
        proto = d.get("protocol", "modbus")
        nodes.append({
            "id": d["id"], "kind": d.get("kind", "plc"), "role": "field-device",
            "model": d.get("model", "PC"),
            "ip": d.get("ip"), "backchannel": d.get("backchannel"),
            "services": [fb.PROTOCOLS[proto]["server"].__name__, "IOModules"],
            "protocol": proto,
            "points": [dict(p) for p in d.get("points", [])],
            "registers": [dict(r) for r in d.get("registers", [])],
        })
    if cfg.get("mtu"):
        m = cfg["mtu"]
        nodes.append({"id": m["id"], "kind": "mtu", "role": "master",
                      "model": m.get("model", "PC"),
                      "ip": m.get("ip"), "services": ["ModbusMaster", "ControlHost"]})
    for r in cfg.get("routers", []):          # L3 node for WAN/routed segments
        nodes.append({"id": r["id"], "kind": "router", "role": "network",
                      "model": r.get("model", "router"), "ip": r.get("ip"),
                      "services": ["IPForward", "OSPFv2", "Zebra"]})
    for sw in cfg.get("switches", []):        # L2 node = one LAN segment
        nodes.append({"id": sw["id"], "kind": sw.get("kind", "switch"), "role": "network",
                      "services": []})
    return {
        "name": cfg.get("name", "scadavt-session"),
        "control": cfg.get("control", {"placement": "mtu"}),
        "netmask": int(cfg.get("netmask", 24)),
        "nodes": nodes,
        "links": list(cfg.get("links", [])),
        "external_apps": list(cfg.get("external_apps", [])),
        "mapping": list(cfg.get("mapping", [])),
        "rules": list(cfg.get("rules", [])),
        "logic": list(cfg.get("logic", [])),
    }


def register_maps(cfg: dict) -> dict:
    """Per-device Modbus register map, annotated with the physical element each
    register is bound to (the ``mapping``). This is the real-PLC view the
    researcher configures against: 'water-level sensor -> PLC2 input register
    30001', 'pump command -> PLC2 coil 00001'. Table is role-fixed; address is
    whatever the configuration pinned or the device auto-assigned."""
    bound = {(m.get("device"), m.get("point")): m.get("element")
             for m in cfg.get("mapping", [])}
    out = {}
    for d in cfg.get("devices", []):
        dev = fb.Device(d["id"], [
            fb.DevicePoint(p["id"], p["role"], bool(p.get("is_bool", False)),
                           address=int(p.get("address", 0)), pinned=("address" in p))
            for p in d.get("points", [])])
        rows = dev.register_map()
        for r in rows:
            r["element"] = bound.get((d["id"], r["point"]))
        out[d["id"]] = rows
    return out


def address_plan(cfg: dict) -> list[dict]:
    """The IP addressing of the emulated network: one row per node with its SCADA
    IP, /mask, role and which LAN segments (switches/routers) it attaches to — the
    'each node has its own IP and can reach the others' view, read off the links."""
    s = session(cfg)
    mask = s["netmask"]
    by_id = {n["id"]: n for n in s["nodes"]}
    attached: dict = {n["id"]: [] for n in s["nodes"]}
    for lk in s["links"]:
        a, b = lk.get("a"), lk.get("b")
        if a in attached and b in attached:
            attached[a].append(b)
            attached[b].append(a)
    rows = []
    for n in s["nodes"]:
        if n["kind"] in ("switch", "hub"):
            continue
        # a router shows its per-link IPs; an endpoint shows its single node IP
        ips = sorted({lk.get(f"{side}_ip") for lk in s["links"] for side, end in
                      (("a", lk.get("a")), ("b", lk.get("b"))) if end == n["id"]
                      and lk.get(f"{side}_ip")})
        if not ips and n.get("ip"):
            ips = [n["ip"]]
        rows.append({"node": n["id"], "kind": n["kind"], "role": n["role"],
                     "model": n.get("model"), "ips": [f"{ip}/{mask}" for ip in ips],
                     "attached_to": attached.get(n["id"], [])})
    return rows


def service_commands(cfg: dict) -> dict:
    """The command each node runs inside its namespace: the config-driven device
    runner on every field node, and the master/control runner on the MTU node."""
    cmds = {}
    for d in cfg["devices"]:
        mb_port = fb.PROTOCOLS[d.get("protocol", "modbus")]["default_port"]
        ip = d.get("ip", "0.0.0.0")
        _, io_port = hostport(d.get("backchannel", ":9161"), 9161)
        cmds[d["id"]] = (f"python3 -m scadavt.core.device --session session.json "
                         f"--node {d['id']} --modbus {ip}:{mb_port} --io 0.0.0.0:{io_port}")
    if cfg.get("mtu") and cfg.get("control", {}).get("placement", "mtu") == "mtu":
        m = cfg["mtu"]
        cmds[m["id"]] = (f"python3 -m scadavt.core.master --session session.json "
                         f"--node {m['id']}")
    return cmds


def gateway_files(cfg: dict) -> tuple[str, str]:
    """Return (endpoints.ini, points.ini) for IOModuleGate, generalised from the
    thesis's two configuration files (Table 3.1)."""
    s = session(cfg)
    # file 1: external-app endpoints + each device's back channel
    ep = []
    for app in s["external_apps"]:
        host, port = hostport(app.get("endpoint", ":0"), 0)
        ep.append(f"[app:{app.get('name','app')}]\nhost: {host}\nport: {port}\n"
                  f"protocol: {app.get('protocol','public')}\n")
    for n in s["nodes"]:
        if n.get("backchannel"):
            host, port = hostport(n["backchannel"], 9161)
            ep.append(f"[{n['id']}]\nip: {host}\nport: {port}\n")
    # file 2: point -> (device, role)
    pts = []
    for m in s["mapping"]:
        pts.append(f"[{m['point']}]\ndevice: {m['device']}\nrole: {m.get('role','measurement')}\n")
    return "\n".join(ep), "\n".join(pts)


def _node_ip(n: dict, lk: dict, side: str) -> str | None:
    """IP for an endpoint on one link: a per-link override (for a router, which
    has one IP per subnet), else the node's own IP. Network nodes have none."""
    if n["kind"] in ("switch", "hub"):
        return None
    return lk.get(f"{side}_ip") or n.get("ip")


def routing_plan(s: dict, mask: int) -> tuple[list[str], dict[str, str]]:
    """Work out the L3 bring-up for a routed (WAN) topology from the session:
    which nodes are routers (need IP forwarding) and, for each endpoint that sits
    on a subnet a router also sits on, the router leg IP to use as its default
    gateway. Returns (router_ids, {endpoint_id: gateway_ip}). Empty for a pure LAN.
    """
    by_id = {n["id"]: n for n in s["nodes"]}
    ifaces = []  # (node_id, ip, kind)
    for lk in s["links"]:
        for side, end in (("a", lk.get("a")), ("b", lk.get("b"))):
            n = by_id.get(end)
            if n is None:
                continue
            ip = _node_ip(n, lk, side)
            if ip:
                ifaces.append((end, ip, n["kind"]))
    router_ids = [n["id"] for n in s["nodes"] if n["kind"] == "router"]
    router_ifaces = [(nid, ip) for nid, ip, kind in ifaces if kind == "router"]

    def net(ip):
        return ipaddress.ip_network(f"{ip}/{mask}", strict=False)

    gateways: dict[str, str] = {}
    for nid, ip, kind in ifaces:
        if kind == "router" or nid in gateways:
            continue
        for _, rip in router_ifaces:
            if net(ip) == net(rip):
                gateways[nid] = rip
                break
    return router_ids, gateways


def core_build_script(cfg: dict) -> str:
    """A runnable CORE gRPC build script.

    It builds the emulated SCADA network the way CORE actually models it
    (CORE 9.x, ``core.api.grpc``): every endpoint node is a Linux-namespace node
    with an **explicit IP on its interface** (``wrappers.Interface(id, ip4=...,
    ip4_mask=...)``); a **SWITCH** node makes a LAN (nodes sharing a subnet reach
    each other directly); a **router** node (``model="router"``, OSPF/IPForward)
    joins two subnets into a **WAN**; per-link impairment is real ``netem`` via
    ``LinkOptions`` (delay in microseconds, loss %, bandwidth bps). After
    ``start_session`` each node's own program is launched **inside its namespace**
    with ``node_command`` — the PLC device runner on field nodes, the master on
    the MTU — so the PLC implementation really runs in the node.

    CORE is not required to *generate* this (so M1 validation is offline); it runs
    as-is on a CORE host with ``core-daemon`` up (as root)."""
    s = session(cfg)
    cmds = service_commands(cfg)
    mask = s["netmask"]
    by_id = {n["id"]: n for n in s["nodes"]}

    L = ["#!/usr/bin/env python3",
         "# Auto-generated by scadavt.core.coregen — domain-neutral CORE session.",
         "# Run on a CORE host with core-daemon running (as root). No manual editing.",
         "from core.api.grpc import client, wrappers",
         "from core.api.grpc.wrappers import NodeType, Position, LinkOptions",
         "",
         "core = client.CoreGrpcClient()      # default endpoint localhost:50051",
         "core.connect()",
         "session = core.create_session()",
         "nodes = {}",
         ""]

    # --- nodes: endpoints are namespace nodes (model PC/router); switches are L2
    for i, n in enumerate(s["nodes"], 1):
        pos = f"Position(x={120 + (i % 6) * 150}, y={160 + (i // 6) * 140})"
        if n["kind"] in ("switch", "hub"):
            ntype = "NodeType.SWITCH" if n["kind"] == "switch" else "NodeType.HUB"
            L.append(f"nodes[{n['id']!r}] = session.add_node({i}, name={n['id']!r}, "
                     f"_type={ntype}, position={pos})")
        else:
            model = n.get("model", "router" if n["kind"] == "router" else "PC")
            L.append(f"nodes[{n['id']!r}] = session.add_node({i}, name={n['id']!r}, "
                     f"_type=NodeType.DEFAULT, model={model!r}, position={pos})"
                     + (f"   # SCADA ip {n['ip']}" if n.get("ip") else ""))
    L.append("")

    # --- links: assign a per-node and per-switch interface index; put the IP on
    #     endpoint interfaces, leave switch interfaces bare (CORE's own pattern)
    ifc: dict = {}

    def iface(node_id: str, lk: dict, side: str) -> str:
        idx = ifc.get(node_id, 0)
        ifc[node_id] = idx + 1
        ip = _node_ip(by_id[node_id], lk, side)
        if ip is None:
            return f"wrappers.Interface({idx})"                 # switch/hub end: no IP
        return f"wrappers.Interface({idx}, ip4={ip!r}, ip4_mask={mask})"

    for lk in s["links"]:
        a, b = lk["a"], lk["b"]
        opts = []
        if lk.get("delay_ms"):
            opts.append(f"delay={int(lk['delay_ms']) * 1000}")   # ms -> microseconds
        if lk.get("loss"):
            opts.append(f"loss={float(lk['loss'])}")
        if lk.get("bw"):
            opts.append(f"bandwidth={int(lk['bw'])}")
        tail = f", options=LinkOptions({', '.join(opts)})" if opts else ""
        L.append(f"session.add_link(node1=nodes[{a!r}], node2=nodes[{b!r}], "
                 f"iface1={iface(a, lk, 'a')}, iface2={iface(b, lk, 'b')}{tail})")

    L += ["", "ok, warnings = core.start_session(session)",
          "print(f'session {session.id} started ok={ok} warnings={warnings}')"]

    # bring up L3 routing for a WAN: IP forwarding on routers + a default route on
    # each endpoint to its subnet's router leg (gRPC-created router models do not
    # reliably start routing, so make it explicit — validated on live CORE)
    router_ids, gateways = routing_plan(s, mask)
    if router_ids:
        L.append("\n# routing bring-up (WAN): IP forwarding + default routes")
        for rid in router_ids:
            L.append(f"core.node_command(session.id, nodes[{rid!r}].id, "
                     f"'sysctl -w net.ipv4.ip_forward=1', shell=True)")
        for nid, gw in gateways.items():
            L.append(f"core.node_command(session.id, nodes[{nid!r}].id, "
                     f"'ip route replace default via {gw}', shell=True)")

    L += ["", "# launch each node's program INSIDE its namespace (the PLC/master runs in-node)"]
    for n in s["nodes"]:
        if n["id"] in cmds:
            run = f"bash -c 'cd \"$SCADAVT_DIR\" && PYTHONPATH=. {cmds[n['id']]} " \
                  f">/tmp/{n['id']}.log 2>&1 &'"
            L.append(f"core.node_command(session.id, nodes[{n['id']!r}].id, {run!r}, "
                     f"wait=False, shell=True)")
    L += ["",
          "# The external process application + IOModuleGate run OUTSIDE CORE, on the",
          "# out-of-band back channel (see gate_endpoints.ini / gate_points.ini).",
          "# SCADAVT_DIR must point at the repo root inside the shared node filesystem.",
          ""]
    return "\n".join(L)


def run_script(cfg: dict) -> str:
    """A host-side helper that launches each field node's device runner and the
    gateway (reference, for a non-CORE or manual bring-up)."""
    cmds = service_commands(cfg)
    lines = ["#!/usr/bin/env bash", "set -euo pipefail",
             "# Launch field-device runners (outside CORE: for local bring-up/testing)."]
    for node_id, cmd in cmds.items():
        lines.append(f"{cmd} &   # {node_id}")
    lines.append("wait")
    return "\n".join(lines)


def write_session(cfg: dict, out_dir: str | Path) -> dict:
    """Write all generation artefacts to ``out_dir``; return the file map."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = session(cfg)
    ep, pts = gateway_files(cfg)
    files = {
        "session.json": json.dumps(s, indent=2),
        "gate_endpoints.ini": ep,
        "gate_points.ini": pts,
        "core_session.py": core_build_script(cfg),
        "run_devices.sh": run_script(cfg),
    }
    for name, text in files.items():
        (out / name).write_text(text)
    return {name: str(out / name) for name in files}
