"""Attack catalogue: declarative attack specs, validated against a scenario.

An attack is data, not code. Each spec names a victim device, a supervised tag
and a simulated-time window; the catalogue validates it and resolves the
network and register facts the attacker container needs. Phase 2 implements
``fdi`` (False Data Injection on a float sensor); replay and command
manipulation are added in phase 3 as further kinds over the same machinery.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import scenario as sc

KINDS = ("fdi",)


@dataclass
class Attack:
    id: str
    kind: str
    plc: str
    tag: str
    start_h: float
    end_h: float
    mode: str = "freeze"
    value: float = 0.0

    @classmethod
    def from_spec(cls, d: dict, index: int) -> "Attack":
        return cls(
            id=str(d.get("id") or f"a{index + 1}"),
            kind=d["kind"],
            plc=d["target"],
            tag=d["tag"],
            start_h=float(d["start_h"]),
            end_h=float(d["end_h"]),
            mode=d.get("mode", "freeze"),
            value=float(d.get("value", 0.0)),
        )


# reg_type -> Modbus read function code
_READ_FC = {"A": 4, "H": 3}


def resolve(atk: Attack, scen: sc.Scenario, plan: dict) -> dict:
    """Validate an attack and return the facts the attacker container needs.

    Raises ValueError with a precise reason if the spec is invalid; this is the
    scientific-integrity gate — an attack may only target a real tag of a real
    device inside the generated topology.
    """
    if atk.kind not in KINDS:
        raise ValueError(f"unknown attack kind {atk.kind!r}")
    if atk.plc not in scen.plcs:
        raise ValueError(f"attack {atk.id}: no such PLC {atk.plc!r}")
    spec = scen.plcs[atk.plc]
    try:
        tag = spec.tag(atk.tag)
    except Exception:
        raise ValueError(f"attack {atk.id}: PLC {atk.plc} has no tag {atk.tag!r}")
    if atk.end_h <= atk.start_h:
        raise ValueError(f"attack {atk.id}: end_h must be after start_h")

    if atk.kind == "fdi":
        if tag.direction != "i":
            raise ValueError(f"attack {atk.id}: FDI target {atk.tag} is not a sensor ('i')")
        if tag.is_bool:
            raise ValueError(f"attack {atk.id}: FDI target {atk.tag} is boolean; "
                             "phase 2 FDI supports float sensors only")
        if tag.reg_type not in _READ_FC:
            raise ValueError(f"attack {atk.id}: tag {atk.tag} has non-readable register "
                             f"type {tag.reg_type!r}")
        if atk.mode not in ("const", "offset", "freeze"):
            raise ValueError(f"attack {atk.id}: unknown FDI mode {atk.mode!r}")

    if atk.plc not in plan["plcs"]:
        raise ValueError(f"attack {atk.id}: PLC {atk.plc} is not in the deployment plan")
    pa = plan["plcs"][atk.plc]
    fa = plan["fields"][pa["field"]]
    return {
        "field": pa["field"],
        "victim_ip": pa["lan"],
        "router_ip": fa["router_lan"],
        "subnet": fa["subnet"],
        "func": _READ_FC[tag.reg_type],
        "address": tag.address,
    }
