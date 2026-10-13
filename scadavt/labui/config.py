"""Build a lab configuration from simple choices (number of PLCs, MTU, protocol).

The result is exactly the config ``scadavt.core.coregen`` consumes, so the UI,
the CLI and hand-written files all speak the same format.
"""

from __future__ import annotations


def make_lab(n_plcs: int = 3, with_mtu: bool = True, protocol: str = "modbus",
             name: str | None = None) -> dict:
    n_plcs = max(1, int(n_plcs))
    devices = [{
        "id": f"PLC{i}", "protocol": protocol, "ip": f"10.0.0.{i}",
        "backchannel": f"172.16.0.{i}:9161",
        "points": [{"id": f"level{i}", "role": "measurement", "is_bool": False},
                   {"id": f"pump{i}", "role": "command", "is_bool": True}],
    } for i in range(1, n_plcs + 1)]
    ids = [d["id"] for d in devices]
    cfg = {
        "name": name or f"lab-{n_plcs}plc",
        "control": {"placement": "mtu" if with_mtu else "plc"},
        "devices": devices,
        "links": [{"a": ids[i], "b": "MTU" if with_mtu else ids[(i + 1) % n_plcs],
                   "delay_ms": 5} for i in range(n_plcs)],
        "external_apps": [{"name": "wds", "endpoint": "127.0.0.1:9000"}],
        "mapping": [m for d in devices for m in (
            {"point": d["points"][0]["id"], "device": d["id"], "role": "measurement"},
            {"point": d["points"][1]["id"], "device": d["id"], "role": "command"})],
    }
    if with_mtu:
        cfg["mtu"] = {"id": "MTU", "ip": "10.0.0.254"}
    return cfg
