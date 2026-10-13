"""M1 tests for the domain-neutral core.

Structured as the proposal's three *independent* acceptance tests plus the
end-to-end runner:

* (a) Integration API alone — extensibility, round trip, non-blocking failure.
* (b) Control engine alone — identical commands across configurable placements.
* (c) CORE networking alone — the field-bus stack over a real socket, plus the
      config-driven session generator validated structurally (CORE not required).
* end-to-end — one control loop over the API in both MTU and local placements,
      and the water example as an external app.
* reproducibility (logical, not CORE wall-clock) and the two-dimensional labels.

CORE itself cannot run in this sandbox (no namespace privileges); the network
behaviour is exercised over loopback, which carries the same Modbus stack that
would traverse CORE on a capable host.
"""

from __future__ import annotations

import asyncio

import pytest

from scadavt.core import coregen, integration as ig, labels
from scadavt.core.control import ControlHost, dict_reader
from scadavt.core.fieldbus import Device, DevicePoint, ModbusClientPlugin, ModbusServer
from scadavt.core.legacy import LegacyBinaryServer
from scadavt.core.timing import Binding, LocalTransport, NetworkTransport, Runner
from scadavt.examples.dummy_app import DummyProcess


def _run(coro):
    return asyncio.run(coro)


def _port(server) -> int:
    return server.sockets[0].getsockname()[1]


# simple hysteresis rules used throughout, written in domain-neutral terms
HYST = [
    {"id": "on", "if": [{"kind": "point", "comp": "x", "attr": "value",
                         "op": "below", "value": 2.0}],
     "then": [{"comp": "u", "kind": "pump", "attr": "status", "value": "open"}]},
    {"id": "off", "if": [{"kind": "point", "comp": "x", "attr": "value",
                          "op": "above", "value": 4.5}],
     "then": [{"comp": "u", "kind": "pump", "attr": "status", "value": "closed"}]},
]


# ---------------------------------------------------------------- (a) API alone
def test_integration_api_roundtrip_and_extensibility():
    async def main():
        app = DummyProcess(x0=3.0)
        server = await app.serve()
        client = ig.IntegrationClient("127.0.0.1", _port(server))
        try:
            hello = await client.hello()
            assert hello["ok"] and hello["api_version"] == ig.API_VERSION
            assert "STEP" in hello["capabilities"]  # negotiated feature
            pts = await client.list_points()
            assert {p.id for p in pts} == {"x_meas", "u_cmd", "u_state"}
            # measurement round trip
            r = await client.acquire("x_meas")
            assert r.quality == ig.OK and abs(r.value - 3.0) < 1e-6
            # command + state feedback
            assert await client.control("u_cmd", 1.0)
            assert (await client.acquire("u_state", ig.STATE)).value == 1.0
        finally:
            await client.close()
            server.close()
    _run(main())


def test_integration_api_extensible_identifiers_and_types():
    """Identifiers are arbitrary length and data types are open — not the
    thesis's 10-byte / 1-byte limits."""
    long_id = "plant.substation_7.feeder_A.voltage_phase_B.rms"  # >10 bytes
    class Powerish(ig.ExternalApp):
        def points(self):
            return [ig.Point(long_id, ig.MEASUREMENT, "voltage", "kV")]
        def acquire(self, pid, role):
            assert pid == long_id
            return 11.5

    async def main():
        server = await Powerish().serve()
        client = ig.IntegrationClient("127.0.0.1", _port(server))
        try:
            pts = await client.list_points()
            assert pts[0].id == long_id and pts[0].dataType == "voltage" and pts[0].unit == "kV"
            assert abs((await client.acquire(long_id)).value - 11.5) < 1e-6
        finally:
            await client.close()
            server.close()
    _run(main())


def test_integration_api_never_blocks_on_failure():
    """A dead endpoint yields FAILED quality within the timeout, never hangs."""
    async def main():
        client = ig.IntegrationClient("127.0.0.1", 1, timeout=0.3)  # port 1: refused
        r = await asyncio.wait_for(client.acquire("x_meas"), timeout=2.0)
        assert r.quality == ig.FAILED
    _run(main())


def test_legacy_binary_adapter_still_works():
    """The thesis 17-byte PDU is preserved behind the compatibility adapter."""
    from scadavt.protocols import wdsproto as wp

    async def main():
        server = await LegacyBinaryServer(DummyProcess(x0=2.5)).serve()
        r, w = await asyncio.open_connection("127.0.0.1", _port(server))
        try:
            w.write(wp.WDSMessage("x_meas", wp.FC_ACQUIRE, wp.PT_NODE, wp.DT_LEVEL).pack())
            await w.drain()
            reply = wp.WDSMessage.unpack(await r.readexactly(wp.PDU_SIZE))
            assert reply.function == wp.FC_ACQUIRE and abs(reply.data - 2.5) < 1e-6
        finally:
            w.close()
            server.close()
    _run(main())


# ------------------------------------------------------------ (b) control alone
def test_control_same_commands_across_placements():
    """Same engine + rules => same command, whether placed in MTU or PLC."""
    values = {("point", "x", "value"): 1.5}  # below 2.0 -> turn on
    cmd_mtu = ControlHost.from_rules(HYST, "mtu").decide(dict_reader(values), 0)
    cmd_plc = ControlHost.from_rules(HYST, "plc").decide(dict_reader(values), 0)
    assert cmd_mtu == cmd_plc
    assert cmd_mtu[0]["comp"] == "u" and cmd_mtu[0]["value"] == 1.0


def test_control_placement_config():
    assert ControlHost.from_rules(HYST, "mtu").needs_mtu is True
    assert ControlHost.from_rules(HYST, "plc").needs_mtu is False
    assert ControlHost.from_rules(HYST, "distributed").needs_mtu is False
    with pytest.raises(ValueError):
        ControlHost.from_rules(HYST, "nonsense")


# -------------------------------------------------------- (c) field-bus + coregen
def test_modbus_fieldbus_roundtrip_over_socket():
    async def main():
        dev = Device("PLC1", [DevicePoint("x_meas", ig.MEASUREMENT, is_bool=False),
                              DevicePoint("u_cmd", ig.COMMAND, is_bool=True)])
        server = await ModbusServer(dev).serve()
        client = ModbusClientPlugin("127.0.0.1", _port(server))
        try:
            dev.set_input("x_meas", 3.75)                       # gateway writes a measurement
            got = await client.read(dev.by_id["x_meas"])        # master reads it over Modbus
            assert abs(got - 3.75) < 1e-4
            await client.write(dev.by_id["u_cmd"], 1.0)         # master writes a command
            assert dev.get_output("u_cmd") == 1.0               # gateway reads it back
        finally:
            await client.close()
            server.close()
    _run(main())


def _cfg_mtu():
    return {
        "name": "cfg-mtu", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC1", "protocol": "modbus", "ip": "10.0.0.1",
                     "backchannel": "172.16.0.1:9161",
                     "points": [{"id": "s1", "role": "measurement", "is_bool": False},
                                {"id": "a1", "role": "command", "is_bool": True}]}],
        "links": [{"a": "PLC1", "b": "MTU", "delay_ms": 5, "loss": 0.0}],
        "external_apps": [{"name": "proc", "endpoint": "127.0.0.1:9000", "protocol": "public"}],
        "mapping": [{"point": "s1", "device": "PLC1", "role": "measurement"},
                    {"point": "a1", "device": "PLC1", "role": "command"}],
    }


def _cfg_local_no_mtu():
    return {
        "name": "cfg-local", "control": {"placement": "plc"},
        "devices": [
            {"id": "RTU_A", "protocol": "modbus", "ip": "10.1.0.1",
             "backchannel": "172.16.0.5:9161",
             "points": [{"id": "m", "role": "measurement", "is_bool": False},
                        {"id": "c", "role": "command", "is_bool": True}]},
            {"id": "RTU_B", "protocol": "modbus", "ip": "10.1.0.2",
             "backchannel": "172.16.0.6:9161",
             "points": [{"id": "m2", "role": "measurement", "is_bool": False}]},
        ],
        "links": [{"a": "RTU_A", "b": "RTU_B", "delay_ms": 0}],
        "external_apps": [{"name": "proc", "endpoint": "127.0.0.1:9100"}],
        "mapping": [{"point": "m", "device": "RTU_A", "role": "measurement"},
                    {"point": "c", "device": "RTU_A", "role": "command"},
                    {"point": "m2", "device": "RTU_B", "role": "measurement"}],
    }


def test_coregen_two_different_configs():
    for cfg in (_cfg_mtu(), _cfg_local_no_mtu()):
        assert coregen.validate(cfg) == []
        s = coregen.session(cfg)
        assert len(s["nodes"]) >= 1
        ep, pts = coregen.gateway_files(cfg)
        assert "port" in ep and "[" in pts           # endpoints + point map produced
        script = coregen.core_build_script(cfg)
        assert "create_session" in script and "add_link" in script
    # local config has no MTU node
    assert all(n["kind"] != "mtu" for n in coregen.session(_cfg_local_no_mtu())["nodes"])


def test_coregen_is_domain_neutral():
    """The generator must not inject any domain vocabulary of its own."""
    out = " ".join(coregen.core_build_script(_cfg_mtu())) \
        + coregen.gateway_files(_cfg_mtu())[0] + coregen.gateway_files(_cfg_mtu())[1]
    low = out.lower()
    for word in ("water", "tank", "pump", "valve", "epanet", "level"):
        assert word not in low


def test_coregen_rejects_bad_config():
    bad = _cfg_mtu()
    bad.pop("mtu")                                   # mtu placement needs an mtu node
    assert any("mtu" in i for i in coregen.validate(bad))
    bad2 = _cfg_mtu()
    bad2["devices"][0]["protocol"] = "profibus"      # not a registered plugin
    assert any("protocol" in i for i in coregen.validate(bad2))


# --------------------------------------------------------------- labels (refinement 2)
def test_labels_window_vs_confirmed():
    samples = [labels.Sample(t=float(i)) for i in range(10)]
    windows = [labels.AttackWindow("dos", "PLC1", start=3.0, end=7.0)]
    # only two timestamps inside the window carry confirmed malicious evidence
    confirmed = {4.0: "src=attacker", 5.0: "altered-cmd"}
    labels.label(samples, windows, confirmed)
    in_win = [s for s in samples if s.attack_window]
    mal = [s for s in samples if s.malicious_confirmed]
    assert [s.t for s in in_win] == [3.0, 4.0, 5.0, 6.0]     # window dimension
    assert [s.t for s in mal] == [4.0, 5.0]                  # confirmed dimension (subset)
    summ = labels.summary(samples)
    assert summ["in_window"] == 4 and summ["confirmed_malicious"] == 2
    assert summ["benign_in_window"] == 2                     # window != malicious
    assert summ["confirmed_outside_window"] == 0


# -------------------------------------------------------- reproducibility (ref. 1)
def _offline_trajectory(seed_steps: int) -> list[float]:
    """Drive a DummyProcess with the hysteresis rule, event-stepped, offline."""
    app = DummyProcess(x0=1.0)
    host = ControlHost.from_rules(HYST, "plc")
    traj = []
    for _ in range(seed_steps):
        read = dict_reader({("point", "x", "value"): app.x})
        for cmd in host.decide(read, 0):
            app.control("u_cmd", cmd["value"])
        app.step(10.0)
        traj.append(round(app.x, 9))
    return traj


def test_logical_reproducibility_but_not_claimed_determinism():
    # same inputs -> identical logical trajectory (decoupled from CORE)
    assert _offline_trajectory(200) == _offline_trajectory(200)
    # and the rule actually drove the process up from a low start
    assert _offline_trajectory(200)[-1] > 1.0


# ------------------------------------------------------------------ end to end
def _bindings():
    return [
        Binding("x_meas", "PLC1", ig.MEASUREMENT, key=("point", "x", "value")),
        Binding("u_state", "PLC1", ig.STATE),
        Binding("u_cmd", "PLC1", ig.COMMAND, comp="u"),
    ]


def _device():
    return Device("PLC1", [
        DevicePoint("x_meas", ig.MEASUREMENT, is_bool=False),
        DevicePoint("u_state", ig.STATE, is_bool=True),
        DevicePoint("u_cmd", ig.COMMAND, is_bool=True),
    ])


def test_e2e_local_placement_closes_loop():
    async def main():
        app = DummyProcess(x0=1.0)                 # starts low -> rule turns actuator on
        server = await app.serve()
        client = ig.IntegrationClient("127.0.0.1", _port(server))
        await client.hello()
        dev = _device()
        host = ControlHost.from_rules(HYST, "plc")
        runner = Runner(client, {"PLC1": dev}, host, _bindings(),
                        LocalTransport({"PLC1": dev}), step_seconds=10.0)
        try:
            recs = await asyncio.wait_for(runner.run(300), timeout=10.0)
            assert app.x > 1.0                                    # process driven up
            assert any(r.commands for r in recs)                  # commands were issued
            assert all(q != ig.FAILED for r in recs for q in r.qualities.values())
        finally:
            await client.close()
            server.close()
    _run(main())


def test_e2e_mtu_placement_uses_network():
    async def main():
        app = DummyProcess(x0=1.0)
        app_server = await app.serve()
        client = ig.IntegrationClient("127.0.0.1", _port(app_server))
        await client.hello()
        dev = _device()
        mb_server = await ModbusServer(dev).serve()               # the emulated-net path
        plugin = ModbusClientPlugin("127.0.0.1", _port(mb_server))
        host = ControlHost.from_rules(HYST, "mtu")
        runner = Runner(client, {"PLC1": dev}, host, _bindings(),
                        NetworkTransport({"PLC1": dev}, {"PLC1": plugin}), step_seconds=10.0)
        try:
            recs = await asyncio.wait_for(runner.run(300), timeout=15.0)
            assert app.x > 1.0
            assert any(r.commands for r in recs)
        finally:
            await plugin.close()
            await client.close()
            mb_server.close()
            app_server.close()
    _run(main())


def test_e2e_water_example_app():
    """Water as an independent external app over the same API (reuses EPANET)."""
    pytest.importorskip("epanet")
    from scadavt.examples.water_app import WaterApp
    from scadavt.protocols import wdsproto as wp

    rules = [{"id": "fill", "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                                     "op": "below", "value": 2.5}],
              "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "open"}]}]

    async def main():
        app = WaterApp()
        server = await app.serve()
        client = ig.IntegrationClient("127.0.0.1", _port(server))
        await client.hello()
        dev = Device("PLC1", [
            DevicePoint("T1_level", ig.MEASUREMENT, is_bool=False),
            DevicePoint("P1_state", ig.STATE, is_bool=True),
            DevicePoint("P1_cmd", ig.COMMAND, is_bool=True)])
        bindings = [Binding("T1_level", "PLC1", ig.MEASUREMENT, key=("tank", "T1", "level")),
                    Binding("P1_state", "PLC1", ig.STATE),
                    Binding("P1_cmd", "PLC1", ig.COMMAND, comp="P1")]
        host = ControlHost.from_rules(rules, "mtu")
        runner = Runner(client, {"PLC1": dev}, host, bindings,
                        LocalTransport({"PLC1": dev}), step_seconds=300.0)
        try:
            start = app.plant.net.get("T1", wp.PT_NODE, wp.DT_LEVEL)
            await asyncio.wait_for(runner.run(12), timeout=30.0)
            end = app.acquire("T1_level", ig.MEASUREMENT)
            assert end > start                                    # rule filled the tank
        finally:
            await client.close()
            server.close()
            app.close()
    _run(main())
