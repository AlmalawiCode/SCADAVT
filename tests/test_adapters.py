"""Adapter layer tests (register milestone M4): adapters, I/O sync, quality,
staleness, fail-safe, deterministic ownership, hardware gating, lifecycle."""

import time

import pytest

from scadavt.core import fieldbus as fb
from scadavt.core.adapters import make_adapter, AdapterError, Sample
from scadavt.core.adapters.simulated import SimulatedAdapter
from scadavt.core.adapters.hardware import HardwareAdapter
from scadavt.core.adapters.simulator import SimulatorAdapter
from scadavt.core.ownership import WriteArbiter
from scadavt.core.iosync import IOSync, DeviceSink


# ---- lifecycle (A10) --------------------------------------------------------
def test_lifecycle_and_idempotent_close():
    a = SimulatedAdapter({"id": "s", "spec": {"t": {"wave": "constant", "value": 3}}})
    assert not a.is_open
    with a as live:
        assert live.is_open
        assert live.read("t").value == 3
    assert not a.is_open
    a.close(); a.close()                         # idempotent


def test_close_runs_on_error_in_context():
    a = SimulatedAdapter({"id": "s", "spec": {}})
    with pytest.raises(RuntimeError):
        with a:
            raise RuntimeError("boom")
    assert not a.is_open                          # released despite the error


# ---- simulated sensor + actuator --------------------------------------------
def test_simulated_sensor_waves_and_actuator():
    a = make_adapter({"id": "sim", "kind": "simulated",
                      "spec": {"humidity": {"wave": "constant", "value": 45},
                               "valve": {"actuator": True}}})
    with a:
        assert a.read("humidity").value == 45
        a.write("valve", 1)                       # simulated actuator responds to a command
        assert a.output("valve") == 1
        assert a.read("valve").value == 1         # reads back the last command


# ---- hardware gating + limits (A9) ------------------------------------------
def test_mock_hardware_works_without_gate():
    a = make_adapter({"id": "hw", "kind": "hardware", "driver": "mock",
                      "inputs": {"temp": 21.5}})
    with a:
        assert a.read("temp").value == 21.5


def test_real_hardware_refused_without_authorization(monkeypatch):
    monkeypatch.delenv("SCADAVT_ALLOW_HARDWARE", raising=False)
    a = make_adapter({"id": "hw", "kind": "hardware", "driver": "serial", "authorized": True})
    with pytest.raises(AdapterError):
        a.open()                                  # gate not set -> refuses, no real I/O


def test_real_hardware_refused_without_authorized_flag(monkeypatch):
    monkeypatch.setenv("SCADAVT_ALLOW_HARDWARE", "1")
    a = make_adapter({"id": "hw", "kind": "hardware", "driver": "serial", "authorized": False})
    with pytest.raises(AdapterError):
        a.open()


def test_output_limits_clamped_and_rejected():
    a = make_adapter({"id": "hw", "kind": "hardware", "driver": "mock",
                      "limits": {"valve": {"min": 0, "max": 1}}})
    with a:
        a.write("valve", 1)
        with pytest.raises(AdapterError):
            a.write("valve", 5)                   # outside safe limits -> rejected


# ---- simulator co-sim -------------------------------------------------------
def test_simulator_process_actuator_feedback():
    a = make_adapter({"id": "sim", "kind": "simulator", "engine": "process",
                      "model": {"level0": 5.0, "fill": 100.0, "drain": 0.0, "max": 10.0}})
    with a:
        a.write("pump", 1)                        # pump on -> level rises
        time.sleep(0.05)
        assert a.read("level").value > 5.0


def test_simulator_unknown_engine():
    a = SimulatorAdapter({"id": "s", "engine": "warp"})
    with pytest.raises(AdapterError):
        a.open()


# ---- ownership arbiter (deterministic) --------------------------------------
def test_arbiter_owner_exclusive():
    arb = WriteArbiter()
    assert arb.resolve("mtu", [("process", 9), ("mtu", 1)]) == 1      # only owner mtu
    assert arb.resolve("mtu", [("process", 9)]) is None               # no mtu proposal


def test_arbiter_priority_and_tie():
    arb = WriteArbiter()
    assert arb.resolve(None, [("mtu", 1), ("process", 2)]) == 2       # process > mtu
    assert arb.resolve(None, [("plc", 1), ("plc", 2)]) == 2           # equal pri -> last


# ---- IO sync: interchangeability (A7) + scaling + quality -------------------
def _device():
    return fb.Device("PLC1", fb.registers_from_config(
        {"id": "PLC1", "registers": [
            {"id": "lvl", "area": "input_register", "data_type": "float32"},
            {"id": "pump", "area": "coil", "data_type": "bool"}]}))


def _run_in(adapter_spec, var="pv", scale=1.0, offset=0.0):
    dev = _device()
    sink = DeviceSink({"PLC1": dev})
    io_map = [{"adapter": "x", "external": {"var": var}, "device": "PLC1",
               "register": "lvl", "direction": "in", "scale": scale, "offset": offset}]
    sync = IOSync({"x": make_adapter({**adapter_spec, "id": "x"})}, io_map, sink)
    with sync:
        sync.cycle()
    return dev.value("lvl")


def test_interchangeable_adapters_same_plc_register():
    # A7: the SAME PLC register 'lvl' is fed by three different adapter kinds,
    # changing only the adapter config.
    sim = _run_in({"kind": "simulated", "spec": {"pv": {"wave": "constant", "value": 7.0}}})
    hw = _run_in({"kind": "hardware", "driver": "mock", "inputs": {"pv": 7.0}})
    proc = _run_in({"kind": "simulator", "engine": "process",
                    "model": {"level0": 7.0, "drain": 0.0}}, var="level")
    assert abs(sim - 7.0) < 1e-6 and abs(hw - 7.0) < 1e-6 and abs(proc - 7.0) < 1e-3


def test_scaling_applied():
    v = _run_in({"kind": "hardware", "driver": "mock", "inputs": {"pv": 100.0}},
                scale=0.1, offset=5.0)
    assert abs(v - 15.0) < 1e-6                   # 100*0.1 + 5


def test_out_direction_actuator_receives_command():
    dev = _device()
    dev.set_output("pump", 1)                     # a command sits in the coil
    sim = make_adapter({"id": "a", "kind": "simulated", "spec": {"valve": {"actuator": True}}})
    sink = DeviceSink({"PLC1": dev})
    sync = IOSync({"a": sim}, [{"adapter": "a", "external": {"var": "valve"},
                                "device": "PLC1", "register": "pump", "direction": "out"}], sink)
    with sync:
        sync.cycle()
    assert sim.output("valve") == 1.0            # actuator applied the register command


# ---- quality / staleness / fail-safe (the user's #2) ------------------------
class _StaleAdapter(SimulatedAdapter):
    def read(self, var):
        return Sample(9.0, "OK", ts=time.monotonic() - 100.0)   # old timestamp


def test_stale_sample_triggers_failsafe_hold_last():
    dev = _device()
    sink = DeviceSink({"PLC1": dev})
    m = {"adapter": "x", "external": {"var": "pv"}, "device": "PLC1", "register": "lvl",
         "direction": "in", "max_age": 1.0, "quality": "hold_last"}
    sync = IOSync({"x": _StaleAdapter({"id": "x", "spec": {}})}, [m], sink)
    with sync:
        sync.cycle()
        assert sync.quality[("PLC1", "lvl")] == "STALE"
        assert dev.value("lvl") == 0.0            # nothing held yet -> not actuated on stale data


def test_failed_read_default_failsafe():
    class _Boom(SimulatedAdapter):
        def read(self, var): raise RuntimeError("io error")
    dev = _device()
    sink = DeviceSink({"PLC1": dev})
    m = {"adapter": "x", "external": {"var": "pv"}, "device": "PLC1", "register": "lvl",
         "direction": "in", "quality": "default", "default": 3.0}
    sync = IOSync({"x": _Boom({"id": "x"})}, [m], sink)
    with sync:
        sync.cycle()
    assert sync.quality[("PLC1", "lvl")] == "FAILED"
    assert dev.value("lvl") == 3.0               # fail-safe default applied, PLC not crashed


def test_sync_close_releases_adapters():
    a = SimulatedAdapter({"id": "x", "spec": {}})
    sync = IOSync({"x": a}, [], DeviceSink({}))
    sync.open(); assert a.is_open
    sync.close(); assert not a.is_open
