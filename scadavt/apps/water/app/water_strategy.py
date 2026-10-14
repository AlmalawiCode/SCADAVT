"""The thesis water control (pump groups + the V1 flow algorithm) as an EXAMPLE
control strategy.

It wraps the validated ``scadavt.master.control.Supervisor`` behind the generic
strategy interface (``decide(read, sim_h)``), so the water controller is loaded
by configuration like any other strategy — not embedded in the core. A small
binding maps the Supervisor's ``(plc, tag)`` value keys onto the core's
``(kind, comp, attr)`` read keys.
"""

from __future__ import annotations

from scadavt.master.control import Supervisor
from scadavt.protocols import wdsproto as wp


class WaterStrategy:
    def __init__(self, cfg: dict, tank_max: dict, bindings: dict,
                 v1_control: bool = True):
        """``bindings`` maps each Supervisor ``(plc, tag)`` read key to a core
        ``(kind, comp, attr)`` key."""
        self.sup = Supervisor(cfg, tank_max, v1_control=v1_control)
        self.bindings = bindings

    def decide(self, read, sim_h: float = 0.0) -> list[dict]:
        # assemble the Supervisor's value dict from the core's read()
        values = {}
        for key in self.sup.required_reads():
            kca = self.bindings.get(tuple(key))
            if kca is not None:
                v = read(*kca)
                if v is not None:
                    values[tuple(key)] = v
        cmds = []
        for c in self.sup.decide(values):
            is_status = c.tag.endswith("_ST_O") or c.tag.endswith("_ST")
            cmds.append({"comp": c.tag, "ptype": wp.PT_LINK,
                         "dtype": wp.DT_STATUS if is_status else wp.DT_SETTING,
                         "value": c.value})
        return cmds
