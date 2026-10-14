"""Generate ``scada.yaml``: the SCADA side of the small-town scenario.

Thirty-three PLCs are spread over 14 field sites. Each field site has its own
router that connects it to a wide-area network ("the Internet"), and the MTU
reaches every PLC through this network. Each PLC supervises the end devices
listed in its tag list. A tag binds a PLC register to one quantity of one
EPANET element:

    [tag, element, element_type, quantity, direction]

``direction`` is ``i`` for a value the PLC reads from the plant (sensor) and
``o`` for a value the PLC writes to the plant (actuator). Status quantities are
mapped to discrete inputs (i) and coils (o); all other quantities are float32
values in input registers (i) or holding registers (o), as in the register map
of a real field device.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .build_network import SUBAREAS

HERE = Path(__file__).resolve().parent

FIELDS = {
    "A": ["PLC2"], "B": ["PLC3", "PLC4"], "C": ["PLC5", "PLC8", "PLC9", "PLC10"],
    "D": ["PLC1"], "E": ["PLC11", "PLC12", "PLC13"], "F": ["PLC6", "PLC7"],
    "G": ["PLC14", "PLC15", "PLC16", "PLC17"], "H": ["PLC27", "PLC28"],
    "I": ["PLC21", "PLC25"], "J": ["PLC18", "PLC19", "PLC20"],
    "K": ["PLC22", "PLC23", "PLC24"], "L": ["PLC33", "PLC29"],
    "M": ["PLC30", "PLC31", "PLC32"], "N": ["PLC26"],
}


def tank(t):
    return [[f"{t}_LVL", t, "node", "level", "i"]]


def valve_onoff(v):
    u = v.upper()
    return [[f"{u}_ST_I", v, "link", "status", "i"],
            [f"{u}_ST_O", v, "link", "status", "o"],
            [f"{u}_FLOW", v, "link", "flow", "i"]]


def pump(p):
    return [[f"{p}_ST_I", p, "link", "status", "i"],
            [f"{p}_ST_O", p, "link", "status", "o"],
            [f"{p}_SPD_I", p, "link", "speed", "i"],
            [f"{p}_SPD_O", p, "link", "speed", "o"],
            [f"{p}_PWR", p, "link", "energy", "i"],
            [f"{p}_FLOW", p, "link", "flow", "i"]]


def subarea(sid, vid):
    m = "M" + vid[1:]
    return [[f"{m}_FLOW", vid, "link", "flow", "i"],
            [f"{sid}_PRES", sid, "node", "pressure", "i"],
            [f"{vid}_SET_I", vid, "link", "setting", "i"],
            [f"{vid}_SET_O", vid, "link", "setting", "o"]]


def build_plcs():
    plcs = {
        "PLC1": tank("T2") + valve_onoff("V35a") + valve_onoff("V35b"),
        "PLC2": pump("P1") + pump("P2") + pump("P3"),
        "PLC3": tank("T1") + valve_onoff("V3a") + valve_onoff("V3b"),
        "PLC4": pump("P4") + pump("P5"),
        "PLC6": [["V1_FLOW", "V1", "link", "flow", "i"],
                 ["V1_SET_I", "V1", "link", "setting", "i"],
                 ["V1_SET_O", "V1", "link", "setting", "o"]],
        "PLC7": tank("T3") + valve_onoff("V7"),
        "PLC21": tank("T4") + valve_onoff("V21a") + valve_onoff("V21b"),
        "PLC25": pump("P10") + pump("P11"),
        "PLC26": tank("T5") + valve_onoff("V26a") + valve_onoff("V26b"),
        "PLC27": pump("P6") + pump("P7"),
        "PLC28": pump("P8") + pump("P9"),
        "PLC29": pump("P12"),
        "PLC33": tank("T6") + valve_onoff("V33a") + valve_onoff("V33b"),
    }
    sub_plc = {"A1": "PLC5", "A2": "PLC8", "A3": "PLC9", "A4": "PLC10",
               "B3": "PLC11", "B2": "PLC12", "B1": "PLC13",
               "C1": "PLC14", "C2": "PLC15", "C3": "PLC16", "C4": "PLC17",
               "D1": "PLC18", "D2": "PLC19", "D3": "PLC20",
               "E1": "PLC22", "E2": "PLC23", "E3": "PLC24",
               "F3": "PLC30", "F2": "PLC31", "F1": "PLC32"}
    for sid, _, vid, _, _ in SUBAREAS:
        plcs[sub_plc[sid]] = subarea(sid, vid)
    field_of = {p: f for f, ps in FIELDS.items() for p in ps}
    out = {}
    for name in sorted(plcs, key=lambda s: int(s[3:])):
        out[name] = {"field": field_of[name], "tags": plcs[name]}
    return out


CONTROL = {
    # On/off control of pump groups from the level of the tank they fill.
    "pump_groups": [
        {"name": "works1", "plc": "PLC2", "pumps": ["P1", "P2", "P3"],
         "tank": "T1", "tank_tag": ["PLC3", "T1_LVL"], "on_below": 0.40, "off_above": 0.90},
        {"name": "T1-T2", "plc": "PLC4", "pumps": ["P4", "P5"],
         "tank": "T2", "tank_tag": ["PLC1", "T2_LVL"], "on_below": 0.40, "off_above": 0.90},
        {"name": "works2-T4", "plc": "PLC28", "pumps": ["P8", "P9"],
         "tank": "T4", "tank_tag": ["PLC21", "T4_LVL"], "on_below": 0.40, "off_above": 0.90},
        {"name": "works2-T6", "plc": "PLC27", "pumps": ["P6", "P7"],
         "tank": "T6", "tank_tag": ["PLC33", "T6_LVL"], "on_below": 0.40, "off_above": 0.90},
        {"name": "T4-T5", "plc": "PLC25", "pumps": ["P10", "P11"],
         "tank": "T5", "tank_tag": ["PLC26", "T5_LVL"], "on_below": 0.40, "off_above": 0.90},
    ],
    # Flow control valve V1 between T2 and T3 (supervisory algorithm).
    "v1": {
        "plc": "PLC6", "setpoint_tag": "V1_SET_O",
        "t2": ["PLC1", "T2_LVL"], "t3": ["PLC7", "T3_LVL"],
        "b": ["PLC1", "V35A_FLOW"], "c": ["PLC7", "V7_FLOW"], "f": ["PLC1", "V35B_FLOW"],
        "area_t2": 153.94, "area_t3": 153.94,   # tank cross-section areas (m2)
        "gain": 300.0,                           # L/min per metre of level difference
        "max_flow": 6000.0,
        "fixed_setting": 2600.0,                # setting used when control is off
    },
    "critical_fraction": 0.15,
}


def build(path: Path = HERE / "scada.yaml") -> Path:
    from .build_network import TANKS
    doc = {
        "name": "small-town water distribution system",
        "epanet_file": "town.inp",
        "population": {s[0]: s[3] for s in SUBAREAS},
        "tank_max_level": {t: v[3] for t, v in TANKS.items()},
        "fields": FIELDS,
        "plcs": build_plcs(),
        "control": CONTROL,
    }
    path.write_text("# Generated by scenario.py - edit freely, or edit scenario.py and regenerate.\n"
                    + yaml.safe_dump(doc, sort_keys=False, width=120))
    return path


if __name__ == "__main__":
    print(build())
