"""Preview simulation engine for the lab UI / CLI.

Runs the **real control engine** (``builder.rules.RuleEngine`` hysteresis) over a
lightweight per-PLC process (an integrator: a tank-like level that rises while
its pump runs and falls otherwise). It is domain-neutral and needs no CORE, so a
lab can be built, run and *attacked* instantly from the browser or the terminal.
Real networked runs use the core modules on a CORE host (docs/CORE_RUNBOOK.md).

Attacks are modelled at the effect level, on the common timeline:

* ``dos`` / ``delay`` on a PLC — supervision can't reach it, so its command
  **freezes** (the level then over/under-shoots). Marks the attack window.
* ``integrity`` on a PLC — the command is **forced off** (a malicious write);
  marks both the attack window and *confirmed-malicious* samples.

This keeps the two label dimensions distinct (window vs confirmed), as required.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..builder.rules import RuleEngine

ON_BELOW = 2.5
OFF_ABOVE = 5.0
FILL, DRAIN = 0.09, 0.035


@dataclass
class Attack:
    kind: str            # dos | delay | integrity
    target: str          # PLC id
    start: int           # cycle index
    end: int             # cycle index (exclusive)

    def active(self, t: int) -> bool:
        return self.start <= t < self.end


@dataclass
class Lab:
    config: dict
    step_fill: float = FILL
    step_drain: float = DRAIN
    t: int = 0
    attacks: list = field(default_factory=list)
    trace: list = field(default_factory=list)

    def __post_init__(self):
        self.plcs = [d["id"] for d in self.config["devices"]]
        self.level_pt, self.pump_pt, self.plc_of_pump = {}, {}, {}
        rules = []
        for i, d in enumerate(self.config["devices"]):
            lvl = d["points"][0]["id"]
            pump = d["points"][1]["id"]
            self.level_pt[d["id"]] = lvl
            self.pump_pt[d["id"]] = pump
            self.plc_of_pump[pump] = d["id"]
            rules += [
                {"id": f"on{i}", "if": [{"kind": "point", "comp": lvl, "attr": "value",
                                         "op": "below", "value": ON_BELOW}],
                 "then": [{"comp": pump, "kind": "pump", "attr": "status", "value": "open"}]},
                {"id": f"off{i}", "if": [{"kind": "point", "comp": lvl, "attr": "value",
                                          "op": "above", "value": OFF_ABOVE}],
                 "then": [{"comp": pump, "kind": "pump", "attr": "status", "value": "closed"}]},
            ]
        self.engine = RuleEngine.from_json(rules)
        self.x = {p: 1.0 + 0.4 * i for i, p in enumerate(self.plcs)}
        self.pump = {p: 0 for p in self.plcs}
        self.drift = {p: 0.004 * i for i, p in enumerate(self.plcs)}

    # ------------------------------------------------------------------ attacks
    def add_attack(self, kind: str, target: str, start: int, end: int) -> None:
        if target not in self.plcs:
            raise ValueError(f"unknown target {target!r}")
        self.attacks.append(Attack(kind, target, int(start), int(end)))

    def _active(self, plc: str):
        for a in self.attacks:
            if a.target == plc and a.active(self.t):
                return a
        return None

    # ------------------------------------------------------------------ one step
    def step(self) -> dict:
        read_vals = {("point", self.level_pt[p], "value"): self.x[p] for p in self.plcs}
        read = lambda k, c, a: read_vals.get((k, c, a))
        decided = {}
        for cmd in self.engine.evaluate(read, 0.0):
            decided[self.plc_of_pump[cmd["comp"]]] = int(cmd["value"] >= 0.5)

        rec = {"t": self.t, "attack_window": False, "malicious_confirmed": False}
        for p in self.plcs:
            atk = self._active(p)
            if atk is None:
                if p in decided:
                    self.pump[p] = decided[p]
            elif atk.kind in ("dos", "delay"):
                rec["attack_window"] = True          # command frozen (no update)
            elif atk.kind == "integrity":
                self.pump[p] = 0                     # malicious forced-off
                rec["attack_window"] = True
                rec["malicious_confirmed"] = True
            self.x[p] = min(6.0, max(0.0, self.x[p]
                            + (self.step_fill if self.pump[p] else -self.step_drain)
                            - self.drift[p]))
            rec[f"{p}_level"] = round(self.x[p], 3)
            rec[f"{p}_pump"] = self.pump[p]
            rec[f"{p}_attack"] = atk is not None
        self.trace.append(rec)
        self.t += 1
        return rec

    def run(self, cycles: int) -> "Lab":
        for _ in range(cycles):
            self.step()
        return self

    # ------------------------------------------------------------------ outputs
    def traces(self) -> dict:
        return {p: [r[f"{p}_level"] for r in self.trace] for p in self.plcs}

    def summary(self) -> dict:
        cyc = len(self.trace)
        win = sum(r["attack_window"] for r in self.trace)
        mal = sum(r["malicious_confirmed"] for r in self.trace)
        return {"plcs": len(self.plcs), "cycles": cyc,
                "attack_window_cycles": win, "confirmed_malicious_cycles": mal,
                "benign_in_window": win - mal,
                "attacks": [vars(a) for a in self.attacks]}
