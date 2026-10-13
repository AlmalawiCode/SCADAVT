"""The water simulator as an EXAMPLE external application.

This wraps the existing, validated EPANET plant (``scadavt.wds.model.Plant``)
behind the public Integration API. It lives outside ``scadavt.core`` on purpose:
it is one example process, exactly the kind of independent program another
researcher would write for power or manufacturing. The core imports nothing from
here; it only speaks the API.

Requires the ``epanet`` package (the build-and-solve path). Tests skip when it
is absent.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..core import integration as ig
from ..protocols import wdsproto as wp


# a minimal one-tank/one-pump design, built with the existing generator
_DESIGN = {
    "name": "water-example", "hours": 24,
    "nodes": [
        {"id": "R1", "kind": "reservoir", "x": 0, "y": 0, "head": 90},
        {"id": "J1", "kind": "junction", "x": 1, "y": 0, "elev": 0, "demand": 4},
        {"id": "T1", "kind": "tank", "x": 2, "y": 0, "elev": 20, "init": 2.0,
         "min": 0.1, "max": 6, "diam": 15},
    ],
    "links": [
        {"id": "P1", "kind": "pump", "n1": "R1", "n2": "J1", "power": 60},
        {"id": "Pi1", "kind": "pipe", "n1": "J1", "n2": "T1", "diam": 350},
    ],
}


class WaterApp(ig.ExternalApp):
    """Exposes T1 level (measurement), P1 command, and P1 actual state."""
    features = {"step": True}

    def __init__(self, design: dict | None = None):
        from ..builder import generate
        from .. import scenario as sc
        from ..wds.model import Plant
        self._tmp = tempfile.TemporaryDirectory(prefix="scadavt_water_app_")
        scada = generate.write_scenario(design or _DESIGN, self._tmp.name)
        self.plant = Plant(sc.load(scada), seed=1)

    def points(self):
        return [
            ig.Point("T1_level", ig.MEASUREMENT, "level", "m"),
            ig.Point("P1_cmd", ig.COMMAND, "status"),
            ig.Point("P1_state", ig.STATE, "status"),
        ]

    def acquire(self, point_id: str, role: str) -> float:
        if point_id == "T1_level":
            return self.plant.net.get("T1", wp.PT_NODE, wp.DT_LEVEL)
        if point_id == "P1_state":
            return self.plant.net.get("P1", wp.PT_LINK, wp.DT_STATUS)
        raise KeyError(point_id)

    def control(self, point_id: str, value: float) -> None:
        if point_id == "P1_cmd":
            self.plant.net.set("P1", wp.PT_LINK, wp.DT_STATUS, 1.0 if value >= 0.5 else 0.0)
        else:
            raise KeyError(point_id)

    def clock(self) -> float:
        return float(self.plant.net.t)

    def step(self, seconds: float) -> float:
        self.plant.advance(seconds)
        return float(self.plant.net.t)

    def close(self):
        self.plant.close()
        self._tmp.cleanup()
