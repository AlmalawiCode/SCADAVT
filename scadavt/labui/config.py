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
    links, plcs, rules, devices, mapping = [], [], [], [], []
    for i in range(1, n + 1):
        J, T, P, PI = f"J{i}", f"T{i}", f"P{i}", f"PI{i}"
        y = (i - 1) * 2
        nodes += [{"id": J, "kind": "junction", "x": 1, "y": y, "elev": 0, "demand": 11},
                  {"id": T, "kind": "tank", "x": 2, "y": y, "elev": 20, "init": 3.5,
                   "min": 0.1, "max": 6, "diam": 6}]
        links += [{"id": P, "kind": "pump", "n1": "R1", "n2": J, "power": 11},
                  {"id": PI, "kind": "pipe", "n1": J, "n2": T, "diam": 300}]
        plcs.append({"id": f"PLC{i}", "elements": [T, P]})
        rules += [
            {"id": f"on{i}", "if": [{"kind": "tank", "comp": T, "attr": "level",
                                     "op": "below", "value": 2.5}],
             "then": [{"comp": P, "kind": "pump", "attr": "status", "value": "open"}]},
            {"id": f"off{i}", "if": [{"kind": "tank", "comp": T, "attr": "level",
                                      "op": "above", "value": 5.0}],
             "then": [{"comp": P, "kind": "pump", "attr": "status", "value": "closed"}]},
        ]
        # PLC registers bound to EPANET elements
        devices.append({
            "id": f"PLC{i}", "protocol": protocol, "ip": f"10.0.0.{i}",
            "backchannel": f"172.16.0.{i}:9161",
            "points": [
                {"id": f"{T}_LEV", "role": "measurement", "is_bool": False,
                 "element": T, "quantity": "level", "ptype": "node"},
                {"id": f"{P}_CMD", "role": "command", "is_bool": True,
                 "element": P, "quantity": "status", "ptype": "link"}],
        })
        mapping += [{"point": f"{T}_LEV", "device": f"PLC{i}", "role": "measurement",
                     "element": T, "quantity": "level"},
                    {"point": f"{P}_CMD", "device": f"PLC{i}", "role": "command",
                     "element": P, "quantity": "status"}]

    ids = [d["id"] for d in devices]
    cfg = {
        "name": name or f"wds-lab-{n}plc",
        "control": {"placement": "mtu" if with_mtu else "plc"},
        "epanet": {"name": name or f"wds-lab-{n}plc", "hours": 72,
                   "nodes": nodes, "links": links, "plcs": plcs, "rules": rules},
        "rules": rules,
        "devices": devices,
        "links": [{"a": ids[i], "b": "MTU" if with_mtu else ids[(i + 1) % n],
                   "delay_ms": 5} for i in range(n)],
        "external_apps": [{"name": "wds", "endpoint": "127.0.0.1:9000"}],
        "mapping": mapping,
    }
    if with_mtu:
        cfg["mtu"] = {"id": "MTU", "ip": "10.0.0.254"}
    return cfg
