"""Register-based control logic tests (register milestone M2).

The pure evaluator (central MTU decides what to write) and validation of
register-logic references. Live cross-PLC register control over Modbus and the
shared-address / holding-write semantics are exercised by
tools/live_core_logic.py on a CORE host.
"""

from scadavt.core import master, coregen


def test_logic_reads():
    logic = [{"id": "r1", "if": [{"device": "PLC1", "register": "lvl"}],
              "then": [{"device": "PLC2", "register": "pump", "set": 1}]}]
    assert master.logic_reads(logic) == {("PLC1", "lvl")}


def test_evaluate_all_conditions_true_writes():
    logic = [{"id": "r1",
              "if": [{"device": "PLC1", "register": "lvl", "op": "below", "value": 3.0}],
              "then": [{"device": "PLC2", "register": "pump", "set": 1}]}]
    assert master.evaluate_logic(logic, {("PLC1", "lvl"): 2.0}) == [("PLC2", "pump", 1)]
    assert master.evaluate_logic(logic, {("PLC1", "lvl"): 5.0}) == []            # condition false
    assert master.evaluate_logic(logic, {}) == []                               # missing read


def test_evaluate_operators():
    def rule(op, val):
        return [{"id": "r", "if": [{"device": "D", "register": "x", "op": op, "value": val}],
                 "then": [{"device": "D", "register": "y", "set": 1}]}]
    assert master.evaluate_logic(rule("above", 3), {("D", "x"): 5}) == [("D", "y", 1)]
    assert master.evaluate_logic(rule("at_least", 5), {("D", "x"): 5}) == [("D", "y", 1)]
    assert master.evaluate_logic(rule("equal", 5), {("D", "x"): 5}) == [("D", "y", 1)]
    assert master.evaluate_logic(rule("not_equal", 5), {("D", "x"): 5}) == []


def test_evaluate_multiple_conditions_and_value_key():
    logic = [{"id": "r",
              "if": [{"device": "A", "register": "x", "op": "below", "value": 3},
                     {"device": "B", "register": "y", "op": "above", "value": 10}],
              "then": [{"device": "C", "register": "z", "value": 7}]}]  # 'value' instead of 'set'
    assert master.evaluate_logic(logic, {("A", "x"): 2, ("B", "y"): 11}) == [("C", "z", 7)]
    assert master.evaluate_logic(logic, {("A", "x"): 2, ("B", "y"): 9}) == []


def _cfg_with_logic():
    return {"name": "t", "control": {"placement": "mtu"},
            "mtu": {"id": "MTU", "ip": "10.0.0.254"},
            "devices": [
                {"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus",
                 "registers": [{"id": "lvl", "area": "input_register", "data_type": "float32"}]},
                {"id": "PLC2", "ip": "10.0.0.2", "protocol": "modbus",
                 "registers": [{"id": "pump", "area": "coil", "data_type": "bool"}]}],
            "switches": [{"id": "SW"}],
            "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
                      {"a": "PLC2", "b": "SW", "a_ip": "10.0.0.2"},
                      {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
            "logic": [{"id": "r1",
                       "if": [{"device": "PLC1", "register": "lvl", "op": "below", "value": 3.0}],
                       "then": [{"device": "PLC2", "register": "pump", "set": 1}]}]}


def test_validate_logic_ok():
    assert coregen.validate(_cfg_with_logic()) == []


def test_validate_logic_unknown_register():
    cfg = _cfg_with_logic()
    cfg["logic"][0]["if"][0]["register"] = "ghost"
    assert any("unknown register" in i for i in coregen.validate(cfg))


def test_validate_logic_target_must_be_writable():
    cfg = _cfg_with_logic()
    # target a read-only input register -> rejected
    cfg["logic"][0]["then"][0] = {"device": "PLC1", "register": "lvl", "set": 1}
    assert any("read-only" in i for i in coregen.validate(cfg))


def test_logic_passed_into_session():
    s = coregen.session(_cfg_with_logic())
    assert len(s["logic"]) == 1 and s["logic"][0]["id"] == "r1"
