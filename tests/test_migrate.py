"""Legacy -> register migration + register-native generators + deterministic
control (register milestone M3)."""

from scadavt.core import migrate as mig
from scadavt.core import corelive, coregen, master


# ---- legacy fixture (points + mapping + rules) ------------------------------
def _legacy():
    return {"name": "old", "control": {"placement": "mtu"},
            "mtu": {"id": "MTU", "ip": "10.0.0.254"},
            "devices": [
                {"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus", "seed": 2.0,
                 "points": [{"id": "s", "role": "measurement", "is_bool": False},
                            {"id": "a", "role": "command", "is_bool": True}]},
                {"id": "PLC2", "ip": "10.0.0.2", "protocol": "modbus", "seed": 2.0,
                 "points": [{"id": "s", "role": "measurement", "is_bool": False},
                            {"id": "a", "role": "command", "is_bool": True}]}],
            "switches": [{"id": "SW"}],
            "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
                      {"a": "PLC2", "b": "SW", "a_ip": "10.0.0.2"},
                      {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
            "mapping": [{"point": "s", "device": "PLC1", "role": "measurement", "element": "S1"},
                        {"point": "a", "device": "PLC1", "role": "command", "element": "A1"},
                        {"point": "s", "device": "PLC2", "role": "measurement", "element": "S2"},
                        {"point": "a", "device": "PLC2", "role": "command", "element": "A2"}],
            "rules": [{"id": "r1", "if": [{"kind": "sensor", "comp": "S1", "attr": "value",
                                           "op": "below", "value": 3.0}],
                       "then": [{"comp": "A2", "kind": "pump", "attr": "status", "value": "open"}]}]}


def test_is_legacy():
    assert mig.is_legacy(_legacy()) is True
    assert mig.is_legacy(corelive.grid(2)) is False


def test_migrate_points_to_registers():
    out = mig.migrate(_legacy())
    d = out["devices"][0]
    assert "points" not in d
    regs = {r["id"]: r for r in d["registers"]}
    assert regs["s"]["area"] == "input_register" and regs["s"]["data_type"] == "float32"
    assert regs["a"]["area"] == "coil" and regs["a"]["data_type"] == "bool"


def test_migrate_rules_to_logic():
    out = mig.migrate(_legacy())
    assert "mapping" not in out and "rules" not in out
    lg = out["logic"]
    assert len(lg) == 1
    assert lg[0]["if"][0] == {"device": "PLC1", "register": "s", "op": "below", "value": 3.0}
    assert lg[0]["then"][0] == {"device": "PLC2", "register": "a", "set": 1.0}


def test_migrate_preserves_topology_and_addressing():
    old, out = _legacy(), mig.migrate(_legacy())
    assert out["mtu"] == old["mtu"] and out["links"] == old["links"]
    assert [d["id"] for d in out["devices"]] == [d["id"] for d in old["devices"]]
    assert [d["ip"] for d in out["devices"]] == [d["ip"] for d in old["devices"]]


def test_migrate_is_idempotent():
    once = mig.migrate(_legacy())
    twice = mig.migrate(once)
    assert once == twice


def test_migrate_already_register_is_noop():
    reg = corelive.grid(3)
    reg.pop("_plc_ips", None); reg.pop("_seg_of", None)
    assert mig.migrate(reg) == reg


def test_migrated_config_validates():
    assert coregen.validate(mig.migrate(_legacy())) == []


def test_then_value_mapping():
    assert mig._then_value("open") == 1.0 and mig._then_value("closed") == 0.0
    assert mig._then_value(0.7) == 0.7


# ---- generators are register-native (no sensor/actuator) --------------------
def test_grid_emits_registers_and_logic_no_legacy():
    cfg = corelive.grid(3)
    assert "mapping" not in cfg and "rules" not in cfg
    for d in cfg["devices"]:
        assert "points" not in d and d["registers"]
        areas = {r["id"]: r["area"] for r in d["registers"]}
        assert areas == {"s": "input_register", "a": "coil"}
    assert len(cfg["logic"]) == 3
    assert coregen.validate(cfg) == []


# ---- deterministic control --------------------------------------------------
def test_conflicting_rules_last_writer_wins():
    logic = [{"id": "r1", "if": [{"device": "D", "register": "x", "op": "below", "value": 9}],
              "then": [{"device": "D", "register": "y", "set": 1}]},
             {"id": "r2", "if": [{"device": "D", "register": "x", "op": "below", "value": 9}],
              "then": [{"device": "D", "register": "y", "set": 0}]}]
    writes = master.evaluate_logic(logic, {("D", "x"): 2})
    assert writes == [("D", "y", 1), ("D", "y", 0)]          # order preserved
    decided = {}
    for did, rid, v in writes:
        decided[(did, rid)] = v                              # the master's collapse
    assert decided == {("D", "y"): 0}                        # deterministic: last wins


def test_failed_or_stale_read_does_not_fire():
    logic = [{"id": "r", "if": [{"device": "D", "register": "x", "op": "below", "value": 3}],
              "then": [{"device": "D", "register": "y", "set": 1}]}]
    # a failed read (value absent) must not actuate — no stale/last value is used
    assert master.evaluate_logic(logic, {}) == []
    # evaluate is stateless: a prior truthy read is not remembered
    assert master.evaluate_logic(logic, {("D", "x"): 2}) == [("D", "y", 1)]
    assert master.evaluate_logic(logic, {}) == []
