"""A tiny, domain-neutral external process — the non-water example used to prove
the core is domain-independent.

It is a single integrator: a scalar reservoir ``x`` that rises while an actuator
``u`` is on and falls while it is off. No water (or any other domain) vocabulary
is baked into the core to run it — the meaning lives entirely here. Points:

    x_meas   MEASUREMENT  current level of x
    u_cmd    COMMAND      actuator on/off (the controller writes this)
    u_state  STATE        the actuator state actually applied (feedback)

A rule such as "x below 2 -> u on; x above 4.5 -> u off" drives it, identically
to how a water rule drives a tank — which is the point.
"""

from __future__ import annotations

from ..core import integration as ig


class DummyProcess(ig.ExternalApp):
    features = {"step": True}

    def __init__(self, x0: float = 3.0, fill: float = 0.02, drain: float = 0.008):
        self.x = float(x0)
        self.u = 0.0
        self.fill, self.drain = fill, drain
        self._t = 0.0

    def points(self):
        return [
            ig.Point("x_meas", ig.MEASUREMENT, "level", "unit"),
            ig.Point("u_cmd", ig.COMMAND, "status"),
            ig.Point("u_state", ig.STATE, "status"),
        ]

    def acquire(self, point_id: str, role: str) -> float:
        if point_id == "x_meas":
            return self.x
        if point_id == "u_state":
            return self.u
        raise KeyError(point_id)

    def control(self, point_id: str, value: float) -> None:
        if point_id == "u_cmd":
            self.u = 1.0 if value >= 0.5 else 0.0
        else:
            raise KeyError(point_id)

    def clock(self) -> float:
        return self._t

    def step(self, seconds: float) -> float:
        self.x += (self.fill if self.u >= 0.5 else -self.drain) * seconds
        self.x = max(0.0, self.x)
        self._t += seconds
        return self._t
