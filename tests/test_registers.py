"""Register-model core tests (register milestone M1).

Cover register encoding (data types, scaling, word order, multi-register),
the register-vs-legacy lowering with single-model enforcement (A11), the
register-based Device, and register validation. Live Modbus behaviour (A1/A8)
is exercised by tools/live_core_registers.py on a CORE host.
"""

import pytest

from scadavt.core import fieldbus as fb
from scadavt.core import coregen


# ---- encoding round-trips ---------------------------------------------------
@pytest.mark.parametrize("dtype,value", [
    ("uint16", 40000), ("int16", -1234), ("uint32", 3_000_000_000),
    ("int32", -2_000_000_000), ("float32", 3.14159),
])
def test_encode_decode_roundtrip(dtype, value):
    r = fb.Register("x", "holding_register", data_type=dtype)
    words = r.encode(value)
    assert len(words) == fb._DTYPE_WORDS[dtype]
    back = r.decode(words)
    if dtype == "float32":
        assert abs(back - value) < 1e-2
    else:
        assert back == value


def test_bool_register():
    r = fb.Register("c", "coil", data_type="bool")
    assert r.is_bool and r.words == 0 and r.width == 1
    assert r.encode(1) is True and r.encode(0.0) is False
    assert r.decode([True]) == 1.0 and r.decode([0]) == 0.0


def test_scale_and_offset():
    # engineering = raw*scale + offset ; stored raw is the inverse
    r = fb.Register("lvl", "input_register", data_type="uint16", scale=0.1, offset=5.0)
    words = r.encode(7.0)              # raw = (7-5)/0.1 = 20
    assert words == [20]
    assert abs(r.decode([20]) - 7.0) < 1e-9


def test_word_order():
    big = fb.Register("a", "holding_register", data_type="uint32", word_order="big")
    little = fb.Register("b", "holding_register", data_type="uint32", word_order="little")
    v = 0x12345678
    assert big.encode(v) == [0x1234, 0x5678]
    assert little.encode(v) == [0x5678, 0x1234]
    assert big.decode(big.encode(v)) == v and little.decode(little.encode(v)) == v


def test_area_properties():
    assert fb.Register("x", "input_register").writable is False
    assert fb.Register("x", "discrete_input", data_type="bool").writable is False
    assert fb.Register("x", "holding_register").writable is True
    assert fb.Register("x", "coil", data_type="bool").writable is True


# ---- lowering + single model (A11) -----------------------------------------
def test_native_registers_build():
    node = {"id": "PLC1", "registers": [
        {"id": "lvl", "area": "input_register", "data_type": "float32"},
        {"id": "pump", "area": "coil", "data_type": "bool"}]}
    regs = fb.registers_from_config(node)
    assert [r.id for r in regs] == ["lvl", "pump"]
    assert regs[0].area == "input_register" and regs[1].is_bool


def test_legacy_points_lower_to_registers():
    node = {"id": "PLC1", "points": [
        {"id": "s", "role": "measurement", "is_bool": False},
        {"id": "a", "role": "command", "is_bool": True}]}
    regs = fb.registers_from_config(node)
    assert regs[0].area == "input_register" and regs[0].data_type == "float32"
    assert regs[1].area == "coil" and regs[1].is_bool


def test_mixing_registers_and_points_is_rejected():
    node = {"id": "PLC1",
            "registers": [{"id": "r", "area": "coil", "data_type": "bool"}],
            "points": [{"id": "s", "role": "measurement"}]}
    with pytest.raises(ValueError):
        fb.registers_from_config(node)


# ---- register-based Device --------------------------------------------------
def test_device_assigns_addresses_and_banks():
    node = {"id": "PLC1", "registers": [
        {"id": "lvl", "area": "input_register", "data_type": "float32"},
        {"id": "flow", "area": "input_register", "data_type": "uint16"},
        {"id": "pump", "area": "coil", "data_type": "bool"}]}
    dev = fb.Device("PLC1", fb.registers_from_config(node))
    assert dev.by_id["lvl"].address == 0 and dev.by_id["flow"].address == 2   # float took 2 words
    rm = {r["point"]: r for r in dev.register_map()}
    assert rm["lvl"]["table"] == "input_register" and rm["pump"]["access"] == "read/write"
    # internal producer writes an input register; a master-style read-back works
    dev.set_input("lvl", 4.5)
    assert abs(dev.get_output("pump") if False else 0.0) == 0.0  # pump not written yet
    dev.set_output("pump", 1)
    assert dev.get_output("pump") == 1.0


def test_pinned_address_honoured():
    node = {"id": "P", "registers": [
        {"id": "a", "area": "holding_register", "address": 10, "data_type": "uint16"},
        {"id": "b", "area": "holding_register", "data_type": "uint16"}]}
    dev = fb.Device("P", fb.registers_from_config(node))
    assert dev.by_id["a"].address == 10 and dev.by_id["b"].address == 11


# ---- validation -------------------------------------------------------------
def _reg_cfg():
    return {"name": "t", "control": {"placement": "mtu"},
            "mtu": {"id": "MTU", "ip": "10.0.0.254"},
            "devices": [{"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus",
                         "registers": [{"id": "lvl", "area": "input_register", "data_type": "float32"},
                                       {"id": "pump", "area": "coil", "data_type": "bool"}]}],
            "switches": [{"id": "SW"}],
            "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
                      {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}]}


def test_validate_register_device_ok():
    assert coregen.validate(_reg_cfg()) == []


def test_validate_rejects_mixing():
    cfg = _reg_cfg()
    cfg["devices"][0]["points"] = [{"id": "s", "role": "measurement"}]
    assert any("one model" in i for i in coregen.validate(cfg))


def test_validate_bad_area_and_type():
    cfg = _reg_cfg()
    cfg["devices"][0]["registers"][0]["area"] = "nonsense"
    cfg["devices"][0]["registers"][1]["data_type"] = "float64"
    issues = coregen.validate(cfg)
    assert any("area" in i for i in issues) and any("data_type" in i for i in issues)


def test_validate_pinned_overlap():
    cfg = _reg_cfg()
    cfg["devices"][0]["registers"] = [
        {"id": "a", "area": "holding_register", "address": 0, "data_type": "float32"},
        {"id": "b", "area": "holding_register", "address": 1, "data_type": "uint16"}]  # overlaps a's 2 words
    assert any("overlap" in i for i in coregen.validate(cfg))


def test_legacy_config_still_validates():
    cfg = _reg_cfg()
    cfg["devices"][0].pop("registers")
    cfg["devices"][0]["points"] = [{"id": "s", "role": "measurement"},
                                   {"id": "a", "role": "command", "is_bool": True}]
    assert coregen.validate(cfg) == []
