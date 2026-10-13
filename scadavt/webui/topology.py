"""Build a GUI-ready topology from a scenario (SCADA description + EPANET model).

Combines three validated sources, read-only:
  * ``scadavt.scenario.load`` — PLCs, field sites, tags (what is supervised);
  * the EPANET ``.inp`` — the physical network graph and node coordinates;
  * ``scadavt.deploy.compose.plan`` — the deterministic network address plan
    (routers, PLC IPs, MTU, gateway, WDS server).

The result is JSON the web UI renders directly. Nothing here mutates state.
"""

from __future__ import annotations

from pathlib import Path

from .. import scenario as sc
from ..deploy import compose as dc
from ..protocols import wdsproto as wp
from . import epanet_topo


def _tag_dict(t: sc.Tag) -> dict:
    return {
        "name": t.name,
        "plc": t.plc,
        "element": t.element,
        "ptype": "link" if t.ptype == wp.PT_LINK else "node",
        "dtype": wp.DATA_TYPE_NAMES.get(t.dtype, str(t.dtype)),
        "direction": t.direction,
        "reg_type": t.reg_type,
        "address": t.address,
        "is_bool": t.is_bool,
    }


def build(scenario_path: str | Path | None = None) -> dict:
    """Return the full topology document for the GUI."""
    scen = sc.load(scenario_path or sc.DEFAULT_SCENARIO)
    graph = epanet_topo.parse(scen.epanet_file)
    plan = dc.plan(scen)

    # element -> the PLC and tags that supervise it
    supervised: dict[str, dict] = {}
    for t in scen.all_tags():
        entry = supervised.setdefault(t.element, {"plc": t.plc, "tags": []})
        entry["tags"].append(_tag_dict(t))

    nodes = [{"id": nid, **info} for nid, info in graph["nodes"].items()]
    links = [{"id": lid, **info} for lid, info in graph["links"].items()]

    # bounding box of placed nodes (for the frontend to scale/flip into SVG)
    xs = [n["x"] for n in nodes if n["x"] is not None]
    ys = [n["y"] for n in nodes if n["y"] is not None]
    bbox = {"xmin": min(xs), "xmax": max(xs), "ymin": min(ys), "ymax": max(ys)} if xs else None

    plcs = {
        name: {
            "field": spec.field,
            "elements": sorted({t.element for t in spec.tags}),
            "tags": [_tag_dict(t) for t in spec.tags],
            "ip": plan["plcs"].get(name, {}).get("lan"),
            "back": plan["plcs"].get(name, {}).get("back"),
        }
        for name, spec in scen.plcs.items()
    }

    return {
        "scenario": scen.doc.get("name", Path(scen.path).stem),
        "epanet_file": Path(scen.epanet_file).name,
        "nodes": nodes,
        "links": links,
        "bbox": bbox,
        "supervised": supervised,
        "plcs": plcs,
        "fields": scen.fields,
        "tank_max_level": scen.doc.get("tank_max_level", {}),
        "population": dict(scen.population),
        "network": {
            "fields": plan["fields"],
            "ctrl": plan["ctrl"],
            "gateway": plan["gateway"],
            "wds": plan["wds"],
        },
    }
