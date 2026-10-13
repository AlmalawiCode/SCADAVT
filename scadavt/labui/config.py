"""Build a lab configuration from simple choices (number of PLCs, MTU, protocol).

The config is **EPANET-backed**: it carries a real physical network design under
``epanet`` (reservoir + one tank/pump loop per PLC), the control ``rules``, and
each PLC's **register bindings** to EPANET elements (a level register bound to a
tank's level; a command register bound to a pump's status). The same config also
exposes the ``coregen`` view (``devices``/``mapping``/``links``) for CORE.

So "3 PLCs" means three real tank/pump loops in EPANET, each read/controlled by
its PLC — not a toy. The run engine (``wds_lab``) uses the EPANET library.
"""

from __future__ import annotations


def make_lab(n_plcs: int = 3, with_mtu: bool = True, protocol: str = "modbus",
             name: str | None = None) -> dict:
    n = max(1, int(n_plcs))

    # ---- physical network (EPANET): R1 feeds, per PLC a junction+tank+pump ----
    nodes = [{"id": "R1", "kind": "reservoir", "x": 0, "y": 0, "head": 50}]
    links, plcs = [], []
    for i in range(1, n + 1):
        J, T, P, PI = f"J{i}", f"T{i}", f"P{i}", f"PI{i}"
        y = (i - 1) * 2
        nodes += [{"id": J, "kind": "junction", "x": 1, "y": y, "elev": 0, "demand": 11},
                  {"id": T, "kind": "tank", "x": 2, "y": y, "elev": 20, "init": 3.5,
                   "min": 0.1, "max": 6, "diam": 6}]
        links += [{"id": P, "kind": "pump", "n1": "R1", "n2": J, "power": 11},
                  {"id": PI, "kind": "pipe", "n1": J, "n2": T, "diam": 300}]
        plcs.append({"id": f"PLC{i}", "elements": [T, P]})

    design = {"name": name or f"wds-lab-{n}plc", "hours": 72,
              "nodes": nodes, "links": links}
    # compose_lab derives the register bindings and auto hysteresis rules
    return compose_lab(design, plcs, rules=None, with_mtu=with_mtu,
                       protocol=protocol, name=name or f"wds-lab-{n}plc")


# register bindings derived per EPANET element kind
#   element kind -> list of (suffix, role, is_bool, quantity, ptype)
_REGS = {
    "tank":      [("LEV", "measurement", False, "level", "node")],
    "junction":  [("PRS", "measurement", False, "pressure", "node")],
    "reservoir": [("HEAD", "measurement", False, "head", "node")],
    "pump":      [("CMD", "command", True, "status", "link"),
                  ("FLOW", "measurement", False, "flow", "link")],
    "valve":     [("SET", "command", False, "setting", "link"),
                  ("FLOW", "measurement", False, "flow", "link")],
    "pipe":      [("FLOW", "measurement", False, "flow", "link")],
}


def compose_lab(design: dict, plcs: list, rules: list | None = None,
                with_mtu: bool = True, protocol: str = "modbus",
                name: str | None = None) -> dict:
    """Turn a hand-drawn EPANET ``design`` + PLC element assignments into a full,
    runnable config: the physical network, each PLC's register bindings derived
    from the elements it owns, the control rules (auto hysteresis for a PLC that
    owns both a tank and a pump, unless ``rules`` are given), and the coregen
    (CORE) view. The backend is the single source of truth for this derivation.
    """
    kind_of = {}
    for nnode in design.get("nodes", []):
        kind_of[nnode["id"]] = nnode["kind"]
    for lk in design.get("links", []):
        kind_of[lk["id"]] = lk["kind"]

    devices, mapping = [], []
    for i, p in enumerate(plcs, 1):
        points = []
        for e in p.get("elements", []):
            for suf, role, is_bool, qty, pt in _REGS.get(kind_of.get(e, ""), []):
                pid = f"{e}_{suf}"
                points.append({"id": pid, "role": role, "is_bool": is_bool,
                               "element": e, "quantity": qty, "ptype": pt})
                mapping.append({"point": pid, "device": p["id"], "role": role,
                                "element": e, "quantity": qty})
        devices.append({"id": p["id"], "protocol": protocol, "ip": f"10.0.0.{i}",
                        "backchannel": f"172.16.0.{i}:9161", "points": points})

    # auto rules: tank + pump owned by the same PLC -> hysteresis
    if rules is None:
        rules = []
        for p in plcs:
            tanks = [e for e in p.get("elements", []) if kind_of.get(e) == "tank"]
            pumps = [e for e in p.get("elements", []) if kind_of.get(e) == "pump"]
            if tanks and pumps:
                T, P = tanks[0], pumps[0]
                rules += [
                    {"id": f"on_{P}", "if": [{"kind": "tank", "comp": T, "attr": "level",
                                              "op": "below", "value": 2.5}],
                     "then": [{"comp": P, "kind": "pump", "attr": "status", "value": "open"}]},
                    {"id": f"off_{P}", "if": [{"kind": "tank", "comp": T, "attr": "level",
                                               "op": "above", "value": 5.0}],
                     "then": [{"comp": P, "kind": "pump", "attr": "status", "value": "closed"}]},
                ]

    nm = name or design.get("name", "wds-lab")
    epanet = {"name": nm, "hours": design.get("hours", 72),
              "nodes": design.get("nodes", []), "links": design.get("links", []),
              "plcs": plcs, "rules": rules}
    ids = [d["id"] for d in devices]
    cfg = {
        "name": nm, "control": {"placement": "mtu" if with_mtu else "plc"},
        "epanet": epanet, "rules": rules, "devices": devices,
        "links": [{"a": ids[i], "b": "MTU" if with_mtu else ids[(i + 1) % len(ids)],
                   "delay_ms": 5} for i in range(len(ids))] if ids else [],
        "external_apps": [{"name": "wds", "endpoint": "127.0.0.1:9000"}],
        "mapping": mapping,
    }
    if with_mtu and ids:
        cfg["mtu"] = {"id": "MTU", "ip": "10.0.0.254"}
    return cfg
