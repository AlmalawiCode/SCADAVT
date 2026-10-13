"""Supervisory control logic of the MTU.

The logic is written as a pure decision function over the values that the MTU
has collected from the PLCs, so that the same code runs inside the networked
MTU and inside the fast offline simulator.

Two control loops are implemented.

Pump groups. Each group of pumps fills one tank. The group is switched on
when the level of that tank falls below ``on_below`` (as a fraction of the
tank's maximum level) and off when the level rises above ``off_above``.

Flow balancing between T2 and T3. T2 receives an inflow ``f`` and supplies
area B (demand ``b``) directly and area C through T3 (demand ``c``). The flow
``q`` through the flow control valve V1 decides how the water is shared. If
both tanks should rise or fall at the same speed, the volume balance
    (f - b - q) / A2 = (q - c) / A3
gives q* = (A3 (f - b) + A2 c) / (A2 + A3), where A2 and A3 are the tank
cross-sections. A proportional term K (h2 - h3) then moves water from the
fuller tank to the emptier one, so that the two levels converge. The result is
limited to the range [0, max_flow]. With control disabled, V1 keeps a fixed
setting, which reproduces the behaviour of a plant without supervision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Key = tuple[str, str]  # (plc, tag)


@dataclass
class Command:
    plc: str
    tag: str
    value: float


@dataclass
class Supervisor:
    cfg: dict
    tank_max: dict[str, float]
    v1_control: bool = True
    _pump_on: dict[str, bool] = field(default_factory=dict)
    _v1_last: float | None = None

    def required_reads(self) -> list[Key]:
        keys = [tuple(g["tank_tag"]) for g in self.cfg["pump_groups"]]
        v = self.cfg["v1"]
        keys += [tuple(v[k]) for k in ("t2", "t3", "b", "c", "f")]
        return keys

    def v1_flow(self, t2, t3, b, c, f) -> float:
        v = self.cfg["v1"]
        a2, a3 = v["area_t2"], v["area_t3"]
        q = (a3 * (f - b) + a2 * c) / (a2 + a3) + v["gain"] * (t2 - t3)
        return min(max(q, 0.0), v["max_flow"])

    def decide(self, values: dict[Key, float]) -> list[Command]:
        """Return the commands for this cycle. Missing values keep the old decision."""
        cmds: list[Command] = []
        for g in self.cfg["pump_groups"]:
            level = values.get(tuple(g["tank_tag"]))
            name = g["name"]
            state = self._pump_on.get(name, False)
            if level is not None:
                frac = level / self.tank_max[g["tank"]]
                if frac < g["on_below"]:
                    state = True
                elif frac > g["off_above"]:
                    state = False
            self._pump_on[name] = state
            for p in g["pumps"]:
                cmds.append(Command(g["plc"], f"{p}_ST_O", 1.0 if state else 0.0))
        v = self.cfg["v1"]
        if self.v1_control:
            ins = [values.get(tuple(v[k])) for k in ("t2", "t3", "b", "c", "f")]
            if all(x is not None for x in ins):
                self._v1_last = self.v1_flow(*ins)
        else:
            self._v1_last = v["fixed_setting"]
        if self._v1_last is not None:
            cmds.append(Command(v["plc"], v["setpoint_tag"], self._v1_last))
        return cmds
