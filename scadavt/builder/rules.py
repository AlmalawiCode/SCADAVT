"""Rule engine — the supervisory control a researcher defines in the lab.

This is the modern reconstruction of the original SCADAVT "WaterSim" rule file
(``RULES.txt``): simple IF/THEN rules evaluated against the live plant each
control step, setting pump and valve status or settings. It is what the lab
means by "the PLC controls the plant".

A rule is JSON (what the builder GUI produces), for example:

    {"id": "r1", "priority": 0,
     "if":   [{"comp": "T1", "kind": "tank", "attr": "level", "op": "below", "value": 2.0}],
     "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "open"}]}

Conditions: a tank ``level`` ``above``/``below`` a value; a pump/valve ``status``
``is`` ``open``/``closed``; a ``setting`` ``above``/``below``; or ``system`` ``time``
``>=``/``<`` hours. ``then`` sets a pump/valve ``status`` (open/closed) or ``setting``.
Rules apply in ascending ``priority`` so a higher-priority rule written later wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..protocols import wdsproto as wp

_LINK_KINDS = {"pump", "valve"}


@dataclass
class Condition:
    kind: str            # tank | pump | valve | system
    comp: str            # element id, or "" for system
    attr: str            # level | status | setting | time
    op: str              # above | below | is | >= | <
    value: object        # float, or "open"/"closed"


@dataclass
class Action:
    comp: str
    kind: str            # pump | valve
    attr: str            # status | setting
    value: object        # "open"/"closed" or float


@dataclass
class Rule:
    id: str
    conditions: list[Condition]
    actions: list[Action]
    priority: int = 0

    @classmethod
    def from_json(cls, d: dict, index: int = 0) -> "Rule":
        conds = [Condition(kind=c.get("kind", "tank"), comp=c.get("comp", ""),
                           attr=c.get("attr", "level"), op=c.get("op", "below"),
                           value=_coerce(c.get("value")))
                 for c in d.get("if", [])]
        acts = [Action(comp=a["comp"], kind=a.get("kind", "pump"),
                       attr=a.get("attr", "status"), value=_coerce(a.get("value")))
                for a in d.get("then", [])]
        return cls(id=str(d.get("id") or f"r{index + 1}"), conditions=conds,
                   actions=acts, priority=int(d.get("priority", 0)))


def _coerce(v):
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("open", "closed"):
            return s
        try:
            return float(s)
        except ValueError:
            return v
    return v


def _status_word(val: float) -> str:
    return "open" if val >= 0.5 else "closed"


class RuleEngine:
    """Evaluate rules against the plant and return the commands to apply."""

    def __init__(self, rules: list[Rule]):
        self.rules = sorted(rules, key=lambda r: r.priority)

    @classmethod
    def from_json(cls, rules_json: list[dict]) -> "RuleEngine":
        return cls([Rule.from_json(d, i) for i, d in enumerate(rules_json)])

    def referenced_reads(self) -> list[tuple]:
        """Distinct ``(kind, comp, attr)`` the conditions read, so a live driver can
        pre-fetch them (rule evaluation itself is synchronous)."""
        seen = []
        for r in self.rules:
            for c in r.conditions:
                if c.kind != "system":
                    key = (c.kind, c.comp, c.attr)
                    if key not in seen:
                        seen.append(key)
        return seen

    def _cond_true(self, c: Condition, read, sim_h: float) -> bool:
        if c.kind == "system":                       # SYSTEM TIME >= / < hours
            t = sim_h
            return t >= float(c.value) if c.op == ">=" else t < float(c.value)
        val = read(c.kind, c.comp, c.attr)
        if val is None:
            return False
        if c.attr == "status":
            return _status_word(val) == str(c.value)
        if c.op == "above":
            return val > float(c.value)
        if c.op == "below":
            return val < float(c.value)
        if c.op == ">=":
            return val >= float(c.value)
        if c.op == "<":
            return val < float(c.value)
        return False

    def evaluate(self, read, sim_h: float) -> list[dict]:
        """``read(kind, comp, attr) -> float`` reads the live plant; returns a list
        of commands ``{comp, ptype, dtype, value}`` (last write per target wins)."""
        pending: dict[tuple, dict] = {}
        for rule in self.rules:                      # ascending priority: later wins
            if all(self._cond_true(c, read, sim_h) for c in rule.conditions):
                for a in rule.actions:
                    if a.attr == "status":
                        dtype = wp.DT_STATUS
                        value = 1.0 if str(a.value) == "open" else 0.0
                    else:                            # setting
                        dtype = wp.DT_SETTING
                        value = float(a.value)
                    pending[(a.comp, dtype)] = {"comp": a.comp, "ptype": wp.PT_LINK,
                                                "dtype": dtype, "value": value}
        return list(pending.values())


# --------------------------------------------------- original RULES.txt parser
_RE_TANK = re.compile(r"TANK\s+(\S+)\s+LEVEL\s+(ABOVE|BELOW)\s+([\d.]+)", re.I)
_RE_STAT = re.compile(r"(PUMP|VALVE)\s+(\S+)\s+STATUS\s+IS\s+(OPEN|CLOSED)", re.I)
_RE_SET = re.compile(r"(PUMP|VALVE)\s+(\S+)\s+SETTING\s+IS\s+([\d.]+)", re.I)
_RE_TIME = re.compile(r"SYSTEM\s+TIME\s+(>=|<)\s+([\d.]+)", re.I)
_RE_PRIO = re.compile(r"PRIORITY\s+(\d+)", re.I)


def parse_rules_txt(text: str) -> list[dict]:
    """Parse the original WaterSim ``RULES.txt`` grammar into the JSON rule model."""
    rules, cur, side = [], None, "if"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("RULE"):
            if cur:
                rules.append(cur)
            cur = {"id": "r" + line.split()[1] if len(line.split()) > 1 else f"r{len(rules)+1}",
                   "if": [], "then": [], "priority": 0}
            side = "if"
            continue
        if cur is None:
            continue
        if up.startswith("THEN"):
            side = "then"
            line = line[4:].strip()
            up = line.upper()
        line = re.sub(r"^(AND|IF)\s+", "", line, flags=re.I)
        up = line.upper()
        if (m := _RE_PRIO.search(up)):
            cur["priority"] = int(m.group(1))
            continue
        if (m := _RE_TANK.search(line)):
            cur[side].append({"kind": "tank", "comp": m.group(1), "attr": "level",
                              "op": m.group(2).lower(), "value": float(m.group(3))})
        elif (m := _RE_TIME.search(line)):
            cur[side].append({"kind": "system", "comp": "", "attr": "time",
                              "op": m.group(1), "value": float(m.group(2))})
        elif (m := _RE_SET.search(line)):
            entry = {"kind": m.group(1).lower(), "comp": m.group(2), "attr": "setting",
                     "value": float(m.group(3))}
            cur[side].append({**entry, "op": "is"} if side == "if" else entry)
        elif (m := _RE_STAT.search(line)):
            entry = {"kind": m.group(1).lower(), "comp": m.group(2), "attr": "status",
                     "value": m.group(3).lower()}
            cur[side].append({**entry, "op": "is"} if side == "if" else entry)
    if cur:
        rules.append(cur)
    return rules
