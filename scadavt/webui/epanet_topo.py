"""Parse an EPANET ``.inp`` file into a plain topology (nodes, links, coords).

This reads only the structural sections needed to draw and, later, edit the
network: junctions, reservoirs, tanks, pipes, pumps, valves and the node
coordinates. It does not solve hydraulics (that is the engine's job); it is a
text parser so the web UI can render the real network graph of a scenario.
"""

from __future__ import annotations

from pathlib import Path

# node sections -> node kind
_NODE_SECTIONS = {"JUNCTIONS": "junction", "RESERVOIRS": "reservoir", "TANKS": "tank"}
# link sections -> link kind
_LINK_SECTIONS = {"PIPES": "pipe", "PUMPS": "pump", "VALVES": "valve"}


def _rows(text: str):
    """Yield ``(section, tokens)`` for every data row of an INP file."""
    section = None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()  # drop comments
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().upper()
            continue
        if section is None:
            continue
        yield section, line.split()


def parse(inp_path: str | Path) -> dict:
    """Return ``{"nodes": {...}, "links": {...}}`` for an EPANET model.

    ``nodes[id] = {"kind", "x", "y"}`` (x/y may be ``None`` if not in
    ``[COORDINATES]``). ``links[id] = {"kind", "n1", "n2"}``.
    """
    text = Path(inp_path).read_text()
    nodes: dict[str, dict] = {}
    links: dict[str, dict] = {}
    coords: dict[str, tuple[float, float]] = {}

    for section, tok in _rows(text):
        if section in _NODE_SECTIONS and tok:
            nodes[tok[0]] = {"kind": _NODE_SECTIONS[section], "x": None, "y": None}
        elif section in _LINK_SECTIONS and len(tok) >= 3:
            links[tok[0]] = {"kind": _LINK_SECTIONS[section], "n1": tok[1], "n2": tok[2]}
        elif section == "COORDINATES" and len(tok) >= 3:
            try:
                coords[tok[0]] = (float(tok[1]), float(tok[2]))
            except ValueError:
                pass

    for nid, (x, y) in coords.items():
        if nid in nodes:
            nodes[nid]["x"] = x
            nodes[nid]["y"] = y
    return {"nodes": nodes, "links": links}
