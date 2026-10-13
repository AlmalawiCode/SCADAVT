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

import json
from pathlib import Path

from ..util import hostport
from . import fieldbus as fb
from . import integration as ig


class ConfigError(ValueError):
    pass


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
        for p in d.get("points", []):
            if p.get("role") not in ig.ROLES:
                issues.append(f"{d.get('id')}.{p.get('id')}: bad role {p.get('role')!r}")
    # control placement
    placement = cfg.get("control", {}).get("placement", "mtu")
    if placement not in ("mtu", "plc", "distributed"):
        issues.append(f"bad control placement {placement!r}")
    if placement == "mtu" and not cfg.get("mtu"):
        issues.append("centralized (mtu) placement requires an 'mtu' node")
    # mapping references real devices/points
    known = {(d["id"], p["id"]) for d in devices for p in d.get("points", [])}
    for m in cfg.get("mapping", []):
        if (m.get("device"), m.get("point")) not in known:
            issues.append(f"mapping references unknown point {m.get('device')}.{m.get('point')}")
    # addressing uniqueness (IPs and back channels must not clash)
    ips = [d.get("ip") for d in devices if d.get("ip")]
    if cfg.get("mtu", {}).get("ip"):
        ips.append(cfg["mtu"]["ip"])
    if len(set(ips)) != len(ips):
        issues.append("duplicate node IP addresses")
    bcs = [d.get("backchannel") for d in devices if d.get("backchannel")]
    if len(set(bcs)) != len(bcs):
        issues.append("duplicate back-channel addresses")
    # links must reference known endpoints (devices, mtu or switches)
    endpoints = set(ids) | {cfg.get("mtu", {}).get("id")} \
        | {s.get("id") for s in cfg.get("switches", [])}
    for lk in cfg.get("links", []):
        for end in (lk.get("a"), lk.get("b")):
            if end not in endpoints:
                issues.append(f"link references unknown endpoint {end!r}")
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
            "ip": d.get("ip"), "backchannel": d.get("backchannel"),
            "services": [fb.PROTOCOLS[proto]["server"].__name__, "IOModules"],
            "protocol": proto,
            "points": [dict(p) for p in d.get("points", [])],
        })
    if cfg.get("mtu"):
        m = cfg["mtu"]
        nodes.append({"id": m["id"], "kind": "mtu", "role": "master",
                      "ip": m.get("ip"), "services": ["ModbusMaster", "ControlHost"]})
    for sw in cfg.get("switches", []):
        nodes.append({"id": sw["id"], "kind": sw.get("kind", "switch"), "role": "network",
                      "services": []})
    return {
        "name": cfg.get("name", "scadavt-session"),
        "control": cfg.get("control", {"placement": "mtu"}),
        "nodes": nodes,
        "links": list(cfg.get("links", [])),
        "external_apps": list(cfg.get("external_apps", [])),
        "mapping": list(cfg.get("mapping", [])),
    }


def service_commands(cfg: dict) -> dict:
    """The command each field node runs (the config-driven device runner)."""
    cmds = {}
    for d in cfg["devices"]:
        mb_port = fb.PROTOCOLS[d.get("protocol", "modbus")]["default_port"]
        ip = d.get("ip", "0.0.0.0")
        _, io_port = hostport(d.get("backchannel", ":9161"), 9161)
        cmds[d["id"]] = (f"python -m scadavt.core.device --session session.json "
                         f"--node {d['id']} --modbus {ip}:{mb_port} --io 0.0.0.0:{io_port}")
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


def core_build_script(cfg: dict) -> str:
    """A CORE gRPC build script (text). Runnable on a CORE host; here it is a
    generated artefact validated structurally (CORE is not required to build it)."""
    s = session(cfg)
    cmds = service_commands(cfg)
    lines = [
        "#!/usr/bin/env python3",
        "# Auto-generated by scadavt.core.coregen — domain-neutral CORE session.",
        "# Runs as-is on a CORE host (core-daemon up). No manual editing required.",
        "from core.api.grpc import client",
        "from core.api.grpc.wrappers import Position, NodeType",
        "",
        "core = client.CoreGrpcClient()",
        "core.connect()",
        "session = core.create_session()",
        "nodes = {}",
    ]
    for i, n in enumerate(s["nodes"], 1):
        ntype = "NodeType.SWITCH" if n["kind"] in ("switch", "hub") else \
                ("NodeType.DEFAULT" if n["kind"] != "router" else "NodeType.DEFAULT")
        lines.append(f"nodes[{n['id']!r}] = session.add_node({i}, name={n['id']!r}, "
                     f"_type={ntype}, position=Position(x={100 + (i % 6) * 130}, "
                     f"y={160 + (i // 6) * 130}))")
        if n.get("ip"):
            lines.append(f"#   ip={n['ip']}  services={n['services']}")
        if n["id"] in cmds:
            lines.append(f"#   startup: {cmds[n['id']]}")
    for lk in s["links"]:
        lines.append(f"session.add_link(nodes[{lk['a']!r}], nodes[{lk['b']!r}], "
                     f"options=dict(delay={int(lk.get('delay_ms', 0)) * 1000}, "
                     f"loss={float(lk.get('loss', 0.0))}, bandwidth={int(lk.get('bw', 0))}))")
    lines += ["core.start_session(session)",
              "# each field node's startup command (above) launches its device runner;",
              "# the gateway + external application run outside CORE on the back channel.",
              ""]
    return "\n".join(lines)


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
