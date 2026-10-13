"""Loading a scenario (EPANET model + SCADA description) and its register maps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .protocols import wdsproto as wp

PKG = Path(__file__).resolve().parent
DEFAULT_SCENARIO = PKG / "scenarios" / "town" / "scada.yaml"


@dataclass
class Tag:
    """One process parameter supervised by a PLC."""
    name: str
    plc: str
    element: str
    ptype: int          # wp.PT_NODE or wp.PT_LINK
    dtype: int          # wp.DT_*
    direction: str      # 'i' (sensor) or 'o' (actuator)
    reg_type: str = ""  # 'A' input register, 'H' holding, 'D' discrete input, 'C' coil
    address: int = 0    # Modbus address (float tags use two registers)

    @property
    def is_bool(self) -> bool:
        return self.dtype == wp.DT_STATUS


@dataclass
class PLCSpec:
    name: str
    field: str
    tags: list[Tag] = field(default_factory=list)

    def regmap(self) -> list[list]:
        """Register map in the SCADAVT notation [tag, type, position, dir]."""
        return [[t.name, t.reg_type, t.address, t.direction] for t in self.tags]

    def tag(self, name: str) -> Tag:
        for t in self.tags:
            if t.name == name:
                return t
        raise KeyError(f"{self.name} has no tag {name}")


@dataclass
class Scenario:
    path: Path
    doc: dict
    plcs: dict[str, PLCSpec]

    @property
    def epanet_file(self) -> Path:
        return self.path.parent / self.doc["epanet_file"]

    @property
    def control(self) -> dict:
        return self.doc["control"]

    @property
    def population(self) -> dict[str, int]:
        return self.doc["population"]

    @property
    def fields(self) -> dict[str, list[str]]:
        return self.doc["fields"]

    def all_tags(self) -> list[Tag]:
        return [t for p in self.plcs.values() for t in p.tags]


def assign_registers(spec: PLCSpec) -> None:
    counters = {"A": 0, "H": 0, "D": 0, "C": 0}
    for t in spec.tags:
        if t.is_bool:
            t.reg_type = "D" if t.direction == "i" else "C"
            t.address = counters[t.reg_type]
            counters[t.reg_type] += 1
        else:
            t.reg_type = "A" if t.direction == "i" else "H"
            t.address = counters[t.reg_type]
            counters[t.reg_type] += 2  # float32 = two 16-bit registers


def load(path: str | Path = DEFAULT_SCENARIO) -> Scenario:
    path = Path(path).resolve()
    doc = yaml.safe_load(path.read_text())
    plcs = {}
    for name, p in doc["plcs"].items():
        spec = PLCSpec(name, p["field"])
        for tname, elem, etype, qty, direction in p["tags"]:
            spec.tags.append(Tag(tname, name, elem,
                                 wp.PT_NODE if etype == "node" else wp.PT_LINK,
                                 wp.DATA_TYPE_CODES[qty], direction))
        assign_registers(spec)
        plcs[name] = spec
    return Scenario(path, doc, plcs)
