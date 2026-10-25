"""PLC I/O modules and emulated field devices (scadavt.core.fieldio).

A device may declare DI/DO/AI/AO modules; each channel lowers to a register in
the matching Modbus table, and emulated sensors/actuators wired to channels
lower to simulated adapters + io_map entries. These tests pin the naming, the
Modbus map, the validation rules, and an offline sensor -> register -> logic ->
actuator loop through the real IOSync engine.
"""

import copy

import pytest

from scadavt.core import coregen, fieldio, validation
from scadavt.core.adapters.simulated import SimulatedAdapter
from scadavt.core.device import device_from_node
from scadavt.core.iosync import DeviceSink, IOSync
from scadavt.core.master import evaluate_logic


def _cfg():
    return {
        "schema_version": 1, "name": "io", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.1.254"},
        "devices": [{"id": "PLC1", "ip": "10.0.1.1", "protocol": "modbus",
                     "io_modules": [{"type": "AI", "channels": 4},
                                    {"type": "DI", "channels": 2},
                                    {"type": "DO", "channels": 4},
                                    {"type": "AO", "channels": 2}]}],
        "switches": [{"id": "SW1"}],
        "links": [{"a": "PLC1", "b": "SW1", "a_ip": "10.0.1.1"},
                  {"a": "MTU", "b": "SW1", "a_ip": "10.0.1.254"}],
        "sensors": [{"id": "TT101", "type": "temperature", "device": "PLC1", "channel": "AI0",
                     "wave": "constant", "value": 28.0},
                    {"id": "HT101", "type": "humidity", "device": "PLC1", "channel": "AI1"},
                    {"id": "SW101", "type": "switch", "device": "PLC1", "channel": "DI0",
                     "wave": "constant", "value": 1.0}],
        "actuators": [{"id": "FAN1", "type": "fan", "device": "PLC1", "channel": "DO0"}],
        "logic": [{"id": "on", "if": [{"device": "PLC1", "register": "AI0", "op": "above", "value": 25}],
                   "then": [{"device": "PLC1", "register": "DO0", "set": 1}]},
                  {"id": "off", "if": [{"device": "PLC1", "register": "AI0", "op": "below", "value": 24}],
                   "then": [{"device": "PLC1", "register": "DO0", "set": 0}]}],
    }


# ---- channels + Modbus map ----------------------------------------------------
def test_channel_names_continue_across_modules_of_a_type():
    d = {"id": "P", "io_modules": [{"type": "AI", "channels": 2}, {"type": "DO", "channels": 1},
                                   {"type": "AI", "channels": 2}]}
    ids = [c["id"] for c in fieldio.channels(d)]
    assert ids == ["AI0", "AI1", "DO0", "AI2", "AI3"]
    assert [c["slot"] for c in fieldio.channels(d)] == [1, 1, 2, 3, 3]


def test_each_module_type_lands_in_its_modbus_table():
    t = {r["channel"]: r for r in fieldio.channel_table(_cfg())["PLC1"]}
    # AI -> input registers (float32 = 2 words each), read-only
    assert [t[f"AI{i}"]["ref"] for i in range(4)] == [30001, 30003, 30005, 30007]
    assert t["AI0"]["access"] == "read-only" and t["AI0"]["count"] == 2
    # DI -> discrete inputs, DO -> coils, AO -> holding registers
    assert (t["DI0"]["ref"], t["DI1"]["ref"]) == (10001, 10002)
    assert [t[f"DO{i}"]["ref"] for i in range(4)] == [1, 2, 3, 4]
    assert t["DO0"]["access"] == "read/write"
    assert (t["AO0"]["ref"], t["AO1"]["ref"]) == (40001, 40003)
    # wired channels are labelled
    assert t["AI0"]["wired"] == "TT101 temperature (°C)"
    assert t["DO0"]["wired"] == "FAN1 fan" and t["DO1"]["wired"] is None


def test_channels_pack_after_explicit_registers():
    cfg = _cfg()
    cfg["devices"][0]["registers"] = [{"id": "s", "area": "input_register", "data_type": "float32"}]
    t = {r["channel"]: r for r in fieldio.channel_table(cfg)["PLC1"]}
    assert t["AI0"]["ref"] == 30003                    # after 's' at 30001-30002
    assert validation.validate(cfg) == []


# ---- lowering -----------------------------------------------------------------
def test_expand_lowers_to_registers_adapters_and_io_map():
    cfg = _cfg()
    before = copy.deepcopy(cfg)
    x = fieldio.expand(cfg)
    assert cfg == before                               # input untouched
    regs = {r["id"]: r for r in x["devices"][0]["registers"]}
    assert regs["AI0"]["area"] == "input_register" and regs["AI0"]["owner"] == "process"
    assert regs["DO0"]["area"] == "coil" and regs["DO0"]["owner"] == "mtu"
    ads = {a["id"]: a for a in x["adapters"]}
    assert ads["TT101"] == {"id": "TT101", "kind": "simulated",
                            "spec": {"value": {"wave": "constant", "min": 18.0, "max": 30.0,
                                               "period": 60.0, "value": 28.0}}}
    assert ads["FAN1"]["spec"] == {"value": {"actuator": True}}
    io = {m["adapter"]: m for m in x["io_map"]}
    assert (io["TT101"]["register"], io["TT101"]["direction"]) == ("AI0", "in")
    assert (io["FAN1"]["register"], io["FAN1"]["direction"]) == ("DO0", "out")


def test_config_without_io_is_unchanged():
    plain = {"devices": [{"id": "P", "registers": [{"id": "s", "area": "coil", "data_type": "bool"}]}]}
    assert fieldio.expand(plain) is plain


def test_session_carries_channels_and_wiring():
    s = coregen.session(_cfg())
    node = next(n for n in s["nodes"] if n["id"] == "PLC1")
    assert {"AI0", "DI0", "DO0", "AO1"} <= {r["id"] for r in node["registers"]}
    assert {a["id"] for a in s["adapters"]} == {"TT101", "HT101", "SW101", "FAN1"}
    assert len(s["io_map"]) == 4


# ---- validation -----------------------------------------------------------------
def test_valid_io_config_passes_full_validation():
    assert validation.validate(_cfg()) == []


@pytest.mark.parametrize("mutate, expect", [
    (lambda c: c["devices"][0]["io_modules"].append({"type": "XI", "channels": 2}), "unknown module type"),
    (lambda c: c["devices"][0]["io_modules"].append({"type": "DI", "channels": 0}), "channels must be"),
    (lambda c: c["devices"][0]["io_modules"].append({"type": "DI", "channels": 17}), "channels must be"),
    (lambda c: c["sensors"][0].update(channel="DI1"), "needs a AI channel"),
    (lambda c: c["sensors"][0].update(channel="AI9"), "has no I/O channel 'AI9'"),
    (lambda c: c["sensors"][1].update(channel="AI0"), "already wired"),
    (lambda c: c["sensors"][1].update(id="TT101"), "duplicate id"),
    (lambda c: c["sensors"][0].update(type="radiation"), "unknown type"),
    (lambda c: c["sensors"][0].update(min=40.0, max=10.0), "is above max"),
    (lambda c: c["sensors"][0].update(wave="zigzag"), "unknown wave"),
    (lambda c: c["sensors"][0].update(device="PLC9"), "unknown device"),
    (lambda c: c["actuators"][0].update(channel="AI2"), "needs a DO channel"),
    (lambda c: c["devices"][0].update(registers=[{"id": "AI0", "area": "coil", "data_type": "bool"}]),
     "clashes with an I/O channel"),
])
def test_bad_io_configs_are_rejected(mutate, expect):
    cfg = _cfg()
    mutate(cfg)
    issues = coregen.validate(cfg)                       # the authoritative semantic stage
    assert any(expect in i for i in issues), issues
    assert not validation.is_valid(cfg)                  # and the config is not runnable


def test_logic_may_not_command_an_input_channel():
    cfg = _cfg()
    cfg["logic"][0]["then"][0]["register"] = "AI3"
    assert any("read-only register" in i for i in validation.validate(cfg))


# ---- offline closed loop through the real engine ------------------------------
def test_sensor_feeds_channel_logic_drives_actuator():
    x = fieldio.expand(_cfg())
    s = coregen.session(_cfg())
    devices = {n["id"]: device_from_node(n) for n in s["nodes"] if n["role"] == "field-device"}
    sync = IOSync.from_config(x, DeviceSink(devices))
    with sync:
        sync.cycle()
        plc = devices["PLC1"]
        assert plc.value("AI0") == pytest.approx(28.0)        # temperature sensor -> AI0
        assert 35.0 <= plc.value("AI1") <= 65.0               # humidity default range
        assert plc.value("DI0") == 1.0                        # switch -> DI0
        # the MTU's rules: 28 > 25 -> fan on (hysteresis 'off' rule does not fire)
        writes = evaluate_logic(x["logic"], {("PLC1", "AI0"): plc.value("AI0")})
        assert writes == [("PLC1", "DO0", 1)]
        plc.set_output("DO0", 1)                              # what the MTU writes
        sync.cycle()
        assert sync.adapters["FAN1"].output("value") == 1.0  # actuator received it


def test_square_wave_switch():
    a = SimulatedAdapter({"id": "s", "kind": "simulated",
                          "spec": {"v": {"wave": "square", "min": 0, "max": 1, "period": 10, "duty": 0.5}}})
    a._t0 -= 2.0                                             # t = 2 s: first half -> on
    assert a.read("v").value == 1.0
    a._t0 -= 5.0                                             # t = 7 s: second half -> off
    assert a.read("v").value == 0.0


# ---- service + CLI ------------------------------------------------------------
def test_service_io_endpoints():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from scadavt.service import create_app
    c = TestClient(create_app())
    cat = c.get("/io/catalog").json()
    assert set(cat["modules"]) == {"DI", "DO", "AI", "AO"}
    assert cat["sensors"]["temperature"]["module"] == "AI"
    r = c.post("/io", json={"config": _cfg()}).json()
    assert r["issues"] == []
    assert {row["channel"]: row["ref"] for row in r["devices"]["PLC1"]}["AI0"] == 30001
    # an unfinished config (no MTU, no links yet) still gets its map
    partial = {"devices": [{"id": "PLC1", "io_modules": [{"type": "DO", "channels": 1}]}]}
    assert c.post("/io", json={"config": partial}).json()["devices"]["PLC1"][0]["ref"] == 1


def test_cli_io_verb_prints_modbus_map(tmp_path, capsys):
    import json
    from scadavt.cli import main
    p = tmp_path / "net.json"
    p.write_text(json.dumps(_cfg()))
    assert main(["io", str(p)]) == 0
    out = capsys.readouterr().out
    assert "AI0" in out and "30001" in out and "TT101 temperature (°C)" in out
    assert "00001" in out and "FAN1 fan" in out
