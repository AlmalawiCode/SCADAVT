"""The REAL run engine: EPANET is the simulation, registers are bound to EPANET
elements, and the control loop reads/writes those elements — exactly the thesis
flow (not a toy integrator).

One cycle:
  1. read each PLC's INPUT registers from EPANET  (tank level, flow, ...),
  2. the control engine decides from those readings,
  3. write each OUTPUT register (pump/valve) back INTO EPANET,
  4. advance the EPANET hydraulic simulation one step -> new readings.

It reuses the validated pieces: ``builder.generate`` (design -> EPANET .inp +
register map), ``wds.model.Plant`` (the EPANET engine via its library), and
``builder.rules.RuleEngine`` (the control). Attacks act on the control path:
``dos``/``delay`` freeze a PLC's output writes (the level then over/under-shoots),
``integrity`` forces the output off and marks confirmed-malicious samples.

Requires the ``epanet`` package (the real hydraulic library).
"""

from __future__ import annotations

import tempfile

from scadavt.builder import generate
from scadavt.builder.rules import RuleEngine
from scadavt.protocols import wdsproto as wp


class WdsLab:
    def __init__(self, design: dict, step_seconds: float = 300.0):
        from scadavt import scenario as sc
        from scadavt.wds.model import Plant
        self.design = design
        self.step_seconds = step_seconds
        self._tmp = tempfile.TemporaryDirectory(prefix="scadavt_wdslab_")
        scada = generate.write_scenario(design, self._tmp.name)
        self.plant = Plant(sc.load(scada), seed=1)
        self.engine = RuleEngine.from_json(design.get("rules", []))
        # which elements each PLC owns, and its tank/pump element ids
        self.owner = {}
        self.tank_of, self.pump_of = {}, {}
        for p in design.get("plcs", []):
            for e in p["elements"]:
                self.owner[e] = p["id"]
            tanks = [e for e in p["elements"] if e in self.plant.net.tanks]
            pumps = [e for e in p["elements"] if e in self.plant.net.pumps]
            if tanks:
                self.tank_of[p["id"]] = tanks[0]
            if pumps:
                self.pump_of[p["id"]] = pumps[0]
        self.plcs = [p["id"] for p in design.get("plcs", [])]
        self.t = 0
        self.attacks = []       # {kind, target(plc), start, end}
        self.trace = []

    # -------------------------------------------------------------- attacks
    def add_attack(self, kind: str, target: str, start: int, end: int) -> None:
        if target not in self.plcs:
            raise ValueError(f"unknown target {target!r}")
        self.attacks.append({"kind": kind, "target": target,
                             "start": int(start), "end": int(end)})

    def _active(self, plc: str):
        for a in self.attacks:
            if a["target"] == plc and a["start"] <= self.t < a["end"]:
                return a
        return None

    def _read(self, kind, comp, attr):
        dtype = {"level": wp.DT_LEVEL, "flow": wp.DT_FLOW,
                 "status": wp.DT_STATUS, "pressure": wp.DT_PRESSURE}.get(attr, wp.DT_LEVEL)
        pt = wp.PT_NODE if attr in ("level", "pressure") else wp.PT_LINK
        try:
            return self.plant.net.get(comp, pt, dtype)
        except Exception:
            return None

    # -------------------------------------------------------------- one step
    def step(self) -> dict:
        rec = {"t": self.t, "attack_window": False, "malicious_confirmed": False}
        # dimension (a): attack window is schedule-based (any attack active now)
        rec["attack_window"] = any(self._active(p) is not None for p in self.plcs)
        # 2) decide from REAL EPANET readings
        for cmd in self.engine.evaluate(self._read, self.plant.net.t / 3600.0):
            comp = cmd["comp"]
            plc = self.owner.get(comp)
            atk = self._active(plc) if plc else None
            value = cmd["value"]
            if atk and atk["kind"] in ("dos", "delay"):
                continue                                   # frozen: command not applied
            if atk and atk["kind"] == "integrity":
                value = 0.0                                # malicious forced-off
                rec["malicious_confirmed"] = True          # dimension (b): confirmed
            # 3) write the command back INTO EPANET
            self.plant.net.set(comp, wp.PT_LINK, cmd["dtype"], value)
        # 4) advance the real hydraulic simulation
        self.plant.advance(self.step_seconds)
        self.t += 1
        # record real readings per PLC
        for plc in self.plcs:
            if plc in self.tank_of:
                rec[f"{plc}_level"] = round(self.plant.net.get(
                    self.tank_of[plc], wp.PT_NODE, wp.DT_LEVEL), 3)
            if plc in self.pump_of:
                rec[f"{plc}_pump"] = int(self.plant.net.get(
                    self.pump_of[plc], wp.PT_LINK, wp.DT_STATUS) >= 0.5)
            rec[f"{plc}_attack"] = self._active(plc) is not None
        # element-keyed snapshot, for the live diagram (tanks fill, pumps toggle)
        rec["tanks"] = {t: round(self.plant.net.get(t, wp.PT_NODE, wp.DT_LEVEL), 3)
                        for t in self.plant.net.tanks}
        rec["pumps"] = {p: int(self.plant.net.get(p, wp.PT_LINK, wp.DT_STATUS) >= 0.5)
                        for p in self.plant.net.pumps}
        self.trace.append(rec)
        return rec

    def run(self, cycles: int) -> "WdsLab":
        for _ in range(cycles):
            self.step()
        return self

    def traces(self) -> dict:
        return {plc: [r.get(f"{plc}_level") for r in self.trace]
                for plc in self.plcs if plc in self.tank_of}

    def frames(self) -> list:
        """Per-cycle element snapshot for the live diagram."""
        return [{"t": r["t"], "tanks": r.get("tanks", {}), "pumps": r.get("pumps", {})}
                for r in self.trace]

    def summary(self) -> dict:
        win = sum(r["attack_window"] for r in self.trace)
        mal = sum(r["malicious_confirmed"] for r in self.trace)
        return {"engine": "epanet", "plcs": len(self.plcs), "cycles": len(self.trace),
                "attack_window_cycles": win, "confirmed_malicious_cycles": mal,
                "benign_in_window": win - mal, "attacks": list(self.attacks)}

    def close(self):
        self.plant.close()
        self._tmp.cleanup()
