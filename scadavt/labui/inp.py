"""Parse an EPANET ``.inp`` file (the configuration the researcher builds in
EPANET and uploads) into a design the builder can draw and bind PLCs to.

We extract the elements and their coordinates for display and for PLC
assignment; the uploaded text is kept verbatim under ``inp_text`` so the run
uses the researcher's authoritative network, not a regenerated one.
"""

from __future__ import annotations


def _rows(text: str):
    """Yield (section, tokens) for each non-comment, non-blank data line."""
    section = None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().upper()
            continue
        if section:
            yield section, line.split()


def _f(tok, default=0.0):
    try:
        return float(tok)
    except (ValueError, IndexError, TypeError):
        return default


def parse_inp(text: str) -> dict:
    nodes, links, coords = [], [], {}
    for sec, t in _rows(text):
        if sec == "JUNCTIONS":
            nodes.append({"id": t[0], "kind": "junction", "elev": _f(t[1] if len(t) > 1 else 0),
                          "demand": _f(t[2]) if len(t) > 2 else 0.0})
        elif sec == "RESERVOIRS":
            nodes.append({"id": t[0], "kind": "reservoir", "head": _f(t[1] if len(t) > 1 else 0)})
        elif sec == "TANKS":
            nodes.append({"id": t[0], "kind": "tank", "elev": _f(t[1] if len(t) > 1 else 0),
                          "init": _f(t[2]) if len(t) > 2 else 0.0,
                          "min": _f(t[3]) if len(t) > 3 else 0.0,
                          "max": _f(t[4]) if len(t) > 4 else 0.0,
                          "diam": _f(t[5]) if len(t) > 5 else 0.0})
        elif sec == "PIPES":
            links.append({"id": t[0], "kind": "pipe", "n1": t[1], "n2": t[2]})
        elif sec == "PUMPS":
            links.append({"id": t[0], "kind": "pump", "n1": t[1], "n2": t[2]})
        elif sec == "VALVES":
            links.append({"id": t[0], "kind": "valve", "n1": t[1], "n2": t[2],
                          "vtype": t[4] if len(t) > 4 else "TCV",
                          "setting": _f(t[5]) if len(t) > 5 else 0.0})
        elif sec == "COORDINATES":
            coords[t[0]] = (_f(t[1]), _f(t[2]))
    # attach coordinates (auto-layout any node without one)
    for i, n in enumerate(nodes):
        if n["id"] in coords:
            n["x"], n["y"] = coords[n["id"]]
        else:
            n["x"], n["y"] = float(i % 6), float(i // 6)
    return {"name": "uploaded", "hours": 72, "nodes": nodes, "links": links,
            "inp_text": text}


def summary(design: dict) -> dict:
    kinds = {}
    for e in design.get("nodes", []) + design.get("links", []):
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return {"nodes": len(design.get("nodes", [])), "links": len(design.get("links", [])),
            "by_kind": kinds}
