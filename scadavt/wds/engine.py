"""Step-by-step hydraulic simulation of a water network with EPANET.

The engine keeps one EPANET project open and advances it in fixed hydraulic
steps, so that values can be read and controls applied between steps, exactly
as a real plant is observed and actuated by a control system.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from epanet import toolkit as tk

from ..protocols import wdsproto as wp

_NODE_PROPS = {
    wp.DT_LEVEL: tk.TANKLEVEL,
    wp.DT_PRESSURE: tk.PRESSURE,
    wp.DT_DEMAND: tk.DEMANDFLOW,
    wp.DT_HEAD: tk.HEAD,
    wp.DT_VOLUME: tk.TANKVOLUME,
    wp.DT_DEFICIT: tk.DEMANDDEFICIT,
}
_LINK_PROPS = {
    wp.DT_FLOW: tk.FLOW,
    wp.DT_STATUS: tk.STATUS,
    wp.DT_SETTING: tk.SETTING,
    wp.DT_SPEED: tk.SETTING,  # a pump's setting is its relative speed
    wp.DT_ENERGY: tk.ENERGY,
}


class WaterNetwork:
    """Thread-safe wrapper around one EPANET project."""

    def __init__(self, inp_path: str | Path, hyd_step: int = 300):
        self.inp_path = str(inp_path)
        self.lock = threading.RLock()
        self._tmp = tempfile.TemporaryDirectory(prefix="scadavt_epanet_")
        self.proj = tk.createproject()
        tk.open(self.proj, self.inp_path, str(Path(self._tmp.name) / "r.rpt"), "")
        tk.settimeparam(self.proj, tk.HYDSTEP, hyd_step)
        tk.settimeparam(self.proj, tk.DURATION, 400 * 24 * 3600)
        self.hyd_step = hyd_step
        self._node_index: dict[str, int] = {}
        self._link_index: dict[str, int] = {}
        n_nodes = tk.getcount(self.proj, tk.NODECOUNT)
        n_links = tk.getcount(self.proj, tk.LINKCOUNT)
        for i in range(1, n_nodes + 1):
            self._node_index[tk.getnodeid(self.proj, i)] = i
        for i in range(1, n_links + 1):
            self._link_index[tk.getlinkid(self.proj, i)] = i
        self.tanks = [n for n, i in self._node_index.items()
                      if tk.getnodetype(self.proj, i) == tk.TANK]
        self.pumps = [l for l, i in self._link_index.items()
                      if tk.getlinktype(self.proj, i) == tk.PUMP]
        self.valves = [l for l, i in self._link_index.items()
                       if tk.getlinktype(self.proj, i) in
                       (tk.PRV, tk.PSV, tk.PBV, tk.FCV, tk.TCV, tk.GPV)]
        self.demand_nodes = [n for n, i in self._node_index.items()
                             if tk.getnodetype(self.proj, i) == tk.JUNCTION
                             and tk.getbasedemand(self.proj, i, 1) > 0]
        self.tank_max = {t: tk.getnodevalue(self.proj, self._node_index[t], tk.MAXLEVEL)
                         for t in self.tanks}
        self.tank_min = {t: tk.getnodevalue(self.proj, self._node_index[t], tk.MINLEVEL)
                         for t in self.tanks}
        tk.openH(self.proj)
        tk.initH(self.proj, tk.NOSAVE)
        self.t = tk.runH(self.proj)  # simulated seconds since start

    # ------------------------------------------------------------------ time
    def advance(self, seconds: float) -> int:
        """Advance the simulation by ``seconds`` (rounded to hydraulic steps)."""
        with self.lock:
            target = self.t + seconds
            while self.t < target:
                step = tk.nextH(self.proj)
                if step <= 0:
                    break
                self.t = tk.runH(self.proj)
            return self.t

    # ------------------------------------------------------------ read/write
    def has(self, element: str, ptype: int) -> bool:
        return element in (self._node_index if ptype == wp.PT_NODE else self._link_index)

    def get(self, element: str, ptype: int, dtype: int) -> float:
        with self.lock:
            if ptype == wp.PT_NODE:
                idx = self._node_index[element]
                if dtype == wp.DT_LEVEL:
                    # EN_TANKLEVEL is the initial level; the current level is
                    # the hydraulic head minus the tank bottom elevation
                    return float(tk.getnodevalue(self.proj, idx, tk.HEAD)
                                 - tk.getnodevalue(self.proj, idx, tk.ELEVATION))
                return float(tk.getnodevalue(self.proj, idx, _NODE_PROPS[dtype]))
            idx = self._link_index[element]
            if dtype == wp.DT_STATUS:
                # report the commanded status of pumps and valves
                v = tk.getlinkvalue(self.proj, idx, tk.STATUS)
                return 1.0 if v > 0.5 else 0.0
            return float(tk.getlinkvalue(self.proj, idx, _LINK_PROPS[dtype]))

    def set(self, element: str, ptype: int, dtype: int, value: float) -> None:
        with self.lock:
            if ptype == wp.PT_NODE:
                raise ValueError("node values cannot be controlled")
            idx = self._link_index[element]
            if dtype == wp.DT_STATUS:
                tk.setlinkvalue(self.proj, idx, tk.STATUS, 1 if value >= 0.5 else 0)
            elif dtype in (wp.DT_SETTING, wp.DT_SPEED):
                tk.setlinkvalue(self.proj, idx, tk.SETTING, max(0.0, float(value)))
            else:
                raise ValueError(f"data type {dtype} cannot be controlled")

    def set_demand(self, node: str, lpm: float) -> None:
        with self.lock:
            tk.setnodevalue(self.proj, self._node_index[node], tk.BASEDEMAND, max(0.0, lpm))

    # --------------------------------------------------------------- logging
    def snapshot(self) -> dict[str, float]:
        """Ground-truth state of the plant (used for logging and evaluation)."""
        with self.lock:
            s: dict[str, float] = {"sim_s": self.t}
            for t in self.tanks:
                s[f"{t}_level"] = self.get(t, wp.PT_NODE, wp.DT_LEVEL)
            for p in self.pumps:
                s[f"{p}_status"] = self.get(p, wp.PT_LINK, wp.DT_STATUS)
                s[f"{p}_flow"] = self.get(p, wp.PT_LINK, wp.DT_FLOW)
                s[f"{p}_power"] = self.get(p, wp.PT_LINK, wp.DT_ENERGY)
            for v in self.valves:
                s[f"{v}_flow"] = self.get(v, wp.PT_LINK, wp.DT_FLOW)
                s[f"{v}_setting"] = self.get(v, wp.PT_LINK, wp.DT_SETTING)
                s[f"{v}_status"] = self.get(v, wp.PT_LINK, wp.DT_STATUS)
            for n in self.demand_nodes:
                s[f"{n}_demand"] = self.get(n, wp.PT_NODE, wp.DT_DEMAND)
                s[f"{n}_deficit"] = self.get(n, wp.PT_NODE, wp.DT_DEFICIT)
                s[f"{n}_pressure"] = self.get(n, wp.PT_NODE, wp.DT_PRESSURE)
            return s

    def close(self) -> None:
        with self.lock:
            try:
                tk.closeH(self.proj)
            except Exception:
                pass
            tk.close(self.proj)
            tk.deleteproject(self.proj)
            self._tmp.cleanup()
