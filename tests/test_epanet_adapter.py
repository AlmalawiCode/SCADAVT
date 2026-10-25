"""EPANET-as-simulator-adapter tests (register milestone M5).

EPANET is integrated exclusively through the generic Adapter interface (a
``simulator`` engine registered by the water overlay via the plugin hook). The
generic PLC/core never imports water. Covers: measurement mapping, control
affecting the process, genuine closed-loop response, adapter interchangeability
(A4: same PLC with a synthetic simulator vs EPANET), and quality/fail-safe.
"""

import os

import pytest

pytest.importorskip("epanet")

from scadavt.core import fieldbus as fb
from scadavt.core.adapters import make_adapter, Sample
from scadavt.core.iosync import IOSync, DeviceSink

INP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "scadavt", "apps", "water", "scenarios", "closed_loop_min", "network.inp")

EPANET_SPEC = {"id": "ep", "kind": "simulator", "engine": "epanet",
               "plugin": "scadavt.apps.water.epanet_adapter",
               "model": {"inp": INP, "step": 300}}


def test_epanet_adapter_reads_measurement():
    a = make_adapter(EPANET_SPEC)
    with a:
        lvl = a.read("T1.level")
        assert isinstance(lvl, Sample) and lvl.value > 0.0     # tank level mapped in


def test_epanet_control_affects_process_closed_loop():
    # #3/#4: a pump command changes the process in subsequent steps.
    off = make_adapter(EPANET_SPEC)
    on = make_adapter(EPANET_SPEC)
    with off, on:
        off.write("P1.status", 0)
        on.write("P1.status", 1)
        for _ in range(6):
            off.tick(); on.tick()
        lvl_off = off.read("T1.level").value
        lvl_on = on.read("T1.level").value
    assert lvl_off != lvl_on                      # the control action changed the plant


def _plc_device():
    return fb.Device("PLC1", fb.registers_from_config(
        {"id": "PLC1", "registers": [
            {"id": "level", "area": "input_register", "data_type": "float32", "owner": "process"},
            {"id": "pump", "area": "coil", "data_type": "bool", "owner": "mtu"}]}))


def test_A4_same_plc_with_synthetic_and_epanet():
    # The SAME PLC register config fed by a synthetic process, then by EPANET,
    # changing ONLY the adapter + mapping.
    def feed(adapter_spec, var):
        dev = _plc_device()
        sink = DeviceSink({"PLC1": dev})
        io_map = [{"adapter": "a", "external": {"var": var}, "device": "PLC1",
                   "register": "level", "direction": "in"}]
        sync = IOSync({"a": make_adapter({**adapter_spec, "id": "a"})}, io_map, sink)
        with sync:
            sync.cycle()
        return dev.value("level")

    synth = feed({"kind": "simulator", "engine": "process",
                  "model": {"level0": 4.0, "drain": 0.0}}, "level")
    water = feed({"kind": "simulator", "engine": "epanet",
                  "plugin": "scadavt.apps.water.epanet_adapter",
                  "model": {"inp": INP}}, "T1.level")
    assert synth == 4.0 and water > 0.0           # same PLC, two process models


def test_quality_register_propagates_freshness():
    # #6: a failed measurement sets the companion quality register to 0, so
    # control logic can refuse to act on stale/invalid data.
    class _Boom:
        def tick(self): pass
        def read(self, var): raise RuntimeError("io")
    from scadavt.core.adapters.simulated import SimulatedAdapter
    a = SimulatedAdapter({"id": "a"})
    a.read = lambda var: (_ for _ in ()).throw(RuntimeError("io"))   # force failure
    dev = fb.Device("PLC1", fb.registers_from_config({"id": "PLC1", "registers": [
        {"id": "level", "area": "input_register", "data_type": "float32"},
        {"id": "level_ok", "area": "discrete_input", "data_type": "bool"}]}))
    sink = DeviceSink({"PLC1": dev})
    m = {"adapter": "a", "external": {"var": "x"}, "device": "PLC1", "register": "level",
         "direction": "in", "quality": "fault", "quality_register": "level_ok"}
    with IOSync({"a": a}, [m], sink) as sync:
        sync.cycle()
    assert dev.value("level_ok") == 0.0           # freshness flag = not-ok
    assert sync.quality[("PLC1", "level")] == "FAILED"


def test_failsafe_default_not_holdlast_for_safety_output():
    # #7: hold_last is not assumed universally safe — a safety-critical input can
    # fail-safe to an explicit safe default instead of holding the last value.
    from scadavt.core.adapters.simulated import SimulatedAdapter
    a = SimulatedAdapter({"id": "a"})
    a.read = lambda var: (_ for _ in ()).throw(RuntimeError("io"))
    dev = _plc_device()
    sink = DeviceSink({"PLC1": dev})
    m = {"adapter": "a", "external": {"var": "x"}, "device": "PLC1", "register": "level",
         "direction": "in", "quality": "default", "default": 0.0}
    with IOSync({"a": a}, [m], sink) as sync:
        sync.cycle()
    assert dev.value("level") == 0.0              # explicit safe value, not a held stale one
