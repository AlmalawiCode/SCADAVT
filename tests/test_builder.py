"""Tests for the interactive builder: rule engine, RULES.txt parser, and the
design -> EPANET/scada.yaml generator (with a real solve when EPANET is present)."""

from __future__ import annotations

import pytest

from scadavt.builder import generate
from scadavt.builder.rules import RuleEngine, parse_rules_txt
from scadavt.protocols import wdsproto as wp


def _reader(level, status=0.0):
    return lambda kind, comp, attr: {"level": level, "status": status,
                                     "setting": 0.0}.get(attr)


def test_rule_engine_hysteresis():
    eng = RuleEngine.from_json([
        {"id": "fill", "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                               "op": "below", "value": 2.5}],
         "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "open"}]},
        {"id": "stop", "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                               "op": "above", "value": 4.5}],
         "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "closed"}]},
    ])
    assert eng.evaluate(_reader(2.0), 0) == [
        {"comp": "P1", "ptype": wp.PT_LINK, "dtype": wp.DT_STATUS, "value": 1.0}]
    assert eng.evaluate(_reader(5.0), 0)[0]["value"] == 0.0
    assert eng.evaluate(_reader(3.5), 0) == []        # deadband: keep state


def test_priority_and_time_rule():
    eng = RuleEngine.from_json([
        {"id": "run", "priority": 0, "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                                             "op": "below", "value": 5}],
         "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "open"}]},
        {"id": "night", "priority": 1,
         "if": [{"kind": "system", "attr": "time", "op": ">=", "value": 1},
                {"kind": "system", "attr": "time", "op": "<", "value": 5}],
         "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "closed"}]},
    ])
    # inside the night window the higher-priority rule wins -> closed
    assert eng.evaluate(_reader(2.0), 3)[0]["value"] == 0.0
    # outside it, the level rule runs the pump
    assert eng.evaluate(_reader(2.0), 8)[0]["value"] == 1.0


def test_parse_rules_txt_grammar():
    txt = """
    RULE 5
    IF TANK T1 LEVEL BELOW 10
    THEN PUMP P1 STATUS IS OPEN
    AND  PUMP P2 STATUS IS OPEN

    RULE 15
    IF SYSTEM TIME >= 1
    AND SYSTEM TIME < 5
    THEN PUMP P1 STATUS IS CLOSED
    PRIORITY 1
    """
    rules = parse_rules_txt(txt)
    assert len(rules) == 2
    assert rules[0]["if"][0] == {"kind": "tank", "comp": "T1", "attr": "level",
                                 "op": "below", "value": 10.0}
    assert len(rules[0]["then"]) == 2
    assert rules[1]["priority"] == 1 and rules[1]["if"][0]["kind"] == "system"


def test_generate_inp_has_sections():
    design = {"name": "t", "nodes": [
        {"id": "R1", "kind": "reservoir", "x": 0, "y": 0, "head": 60},
        {"id": "T1", "kind": "tank", "x": 2, "y": 0, "max": 5},
        {"id": "J1", "kind": "junction", "x": 1, "y": 0, "demand": 3}],
        "links": [{"id": "P1", "kind": "pump", "n1": "R1", "n2": "J1", "power": 20},
                  {"id": "Pi1", "kind": "pipe", "n1": "J1", "n2": "T1"}]}
    inp = generate.generate_inp(design)
    for s in ("[TANKS]", "[PUMPS]", "[PIPES]", "[COORDINATES]", "POWER"):
        assert s in inp
    scada = generate.generate_scada(design)
    assert scada["tank_max_level"] == {"T1": 5.0}
    assert scada["epanet_file"] == "plant.inp"


def test_build_generate_and_solve(tmp_path):
    """A built design generates a scenario EPANET actually solves and controls."""
    pytest.importorskip("epanet")
    import scadavt.scenario as sc
    from scadavt.wds.model import Plant
    design = {"name": "demo", "hours": 6, "nodes": [
        {"id": "R1", "kind": "reservoir", "x": 0, "y": 0, "head": 90},
        {"id": "J1", "kind": "junction", "x": 1, "y": 0, "elev": 0, "demand": 4},
        {"id": "T1", "kind": "tank", "x": 2, "y": 0, "elev": 20, "init": 2.0,
         "min": 0.1, "max": 6, "diam": 15}],
        "links": [{"id": "P1", "kind": "pump", "n1": "R1", "n2": "J1", "power": 60},
                  {"id": "Pi1", "kind": "pipe", "n1": "J1", "n2": "T1", "diam": 350}],
        "rules": [{"id": "fill", "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                                         "op": "below", "value": 2.5}],
                   "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "open"}]}]}
    scada = generate.write_scenario(design, tmp_path)
    scen = sc.load(scada)
    plant = Plant(scen, seed=1)
    try:
        start = plant.net.get("T1", wp.PT_NODE, wp.DT_LEVEL)
        eng = RuleEngine.from_json(design["rules"])

        def read(kind, comp, attr):
            d = {"level": wp.DT_LEVEL, "status": wp.DT_STATUS}[attr]
            pt = wp.PT_NODE if attr == "level" else wp.PT_LINK
            return plant.net.get(comp, pt, d)
        for _ in range(12):
            for c in eng.evaluate(read, plant.net.t / 3600.0):
                plant.net.set(c["comp"], c["ptype"], c["dtype"], c["value"])
            plant.advance(300)
        end = plant.net.get("T1", wp.PT_NODE, wp.DT_LEVEL)
    finally:
        plant.close()
    # the rule turned the pump on from a low start and raised the level
    assert end > start
