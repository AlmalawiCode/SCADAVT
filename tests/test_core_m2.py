"""M2 tests: generalized CORE session generator, the config-driven device-node
runner (what a CORE node runs), and communication-layer disturbance measurement.

CORE still cannot run live in this sandbox, so the generator is validated
structurally and over multiple configurations, the device runner is exercised
over loopback (both faces: field-bus + back channel), and the disturbance is
injected by a degrading loopback proxy (a stand-in for CORE netem).
"""

from __future__ import annotations

import asyncio
import json

from scadavt.core import coregen
from scadavt.core import integration as ig
from scadavt.core.device import run_node
from scadavt.core.fieldbus import DevicePoint, ModbusClientPlugin, ModbusServer, Device
from scadavt.core.netmeasure import DegradedProxy, measure_reads
from scadavt.protocols import iomodules as io


def _run(coro):
    return asyncio.run(coro)


def _port(server):
    return server.sockets[0].getsockname()[1]


# ------------------------------------------------- config-driven device runner
async def _io_call(host, port, msg: io.IOMessage) -> io.IOMessage:
    r, w = await asyncio.open_connection(host, port)
    try:
        w.write(msg.pack())
        await w.drain()
        return await io.read_message(r)
    finally:
        w.close()


def test_device_node_runner_both_faces():
    node = {"id": "PLC1", "protocol": "modbus", "points": [
        {"id": "s1", "role": ig.MEASUREMENT, "is_bool": False},
        {"id": "a1", "role": ig.COMMAND, "is_bool": True}]}

    async def main():
        device, fb_server, io_server = await run_node(
            node, ("127.0.0.1", 0), ("127.0.0.1", 0))
        mb_port, io_port = _port(fb_server), _port(io_server)
        mbc = ModbusClientPlugin("127.0.0.1", mb_port)
        try:
            # gateway writes a measurement over the back channel (IOModules)
            reply = await _io_call("127.0.0.1", io_port,
                                   io.IOMessage(1, io.FC_WRITE, "s1", 3.5))
            assert reply.transaction == 1 and abs(reply.data - 3.5) < 1e-6
            # a master reads that measurement over the field bus (Modbus)
            assert abs(await mbc.read(device.by_id["s1"]) - 3.5) < 1e-4
            # the master writes a command over Modbus ...
            await mbc.write(device.by_id["a1"], 1.0)
            # ... and the gateway reads it back over the back channel
            got = await _io_call("127.0.0.1", io_port, io.IOMessage(1, io.FC_READ, "a1"))
            assert got.transaction == 1 and got.data == 1.0
        finally:
            await mbc.close()
            fb_server.close()
            io_server.close()
    _run(main())


# -------------------------------------------------- generalized generator
def _cfg_single_mtu():
    return {"name": "single-mtu", "control": {"placement": "mtu"},
            "mtu": {"id": "MTU", "ip": "10.0.0.254"},
            "devices": [{"id": "PLC1", "protocol": "modbus", "ip": "10.0.0.1",
                         "backchannel": "172.16.0.1:9161",
                         "points": [{"id": "s", "role": "measurement", "is_bool": False},
                                    {"id": "a", "role": "command", "is_bool": True}]}],
            "links": [{"a": "PLC1", "b": "MTU", "delay_ms": 5}],
            "external_apps": [{"name": "proc", "endpoint": "127.0.0.1:9000"}],
            "mapping": [{"point": "s", "device": "PLC1", "role": "measurement"},
                        {"point": "a", "device": "PLC1", "role": "command"}]}


def _cfg_local_pair():
    return {"name": "local-pair", "control": {"placement": "plc"},
            "devices": [{"id": "RTU_A", "ip": "10.1.0.1", "backchannel": "172.16.0.5:9161",
                         "points": [{"id": "m", "role": "measurement", "is_bool": False},
                                    {"id": "c", "role": "command", "is_bool": True}]},
                        {"id": "RTU_B", "ip": "10.1.0.2", "backchannel": "172.16.0.6:9161",
                         "points": [{"id": "m2", "role": "measurement", "is_bool": False}]}],
            "links": [{"a": "RTU_A", "b": "RTU_B"}],
            "external_apps": [{"name": "proc", "endpoint": "127.0.0.1:9100"}],
            "mapping": [{"point": "m", "device": "RTU_A", "role": "measurement"},
                        {"point": "c", "device": "RTU_A", "role": "command"},
                        {"point": "m2", "device": "RTU_B", "role": "measurement"}]}


def _cfg_switched_many():
    devices = [{"id": f"PLC{i}", "ip": f"10.2.0.{i}", "backchannel": f"172.16.1.{i}:9161",
                "points": [{"id": f"s{i}", "role": "measurement", "is_bool": False}]}
               for i in range(1, 4)]
    return {"name": "switched", "control": {"placement": "mtu"},
            "mtu": {"id": "MTU", "ip": "10.2.0.254"},
            "switches": [{"id": "SW1"}],
            "devices": devices,
            "links": [{"a": f"PLC{i}", "b": "SW1", "delay_ms": i} for i in range(1, 4)]
                     + [{"a": "SW1", "b": "MTU"}],
            "external_apps": [{"name": "proc", "endpoint": "127.0.0.1:9200"}],
            "mapping": [{"point": f"s{i}", "device": f"PLC{i}", "role": "measurement"}
                        for i in range(1, 4)]}


def test_generator_three_configurations(tmp_path):
    for cfg in (_cfg_single_mtu(), _cfg_local_pair(), _cfg_switched_many()):
        assert coregen.validate(cfg) == []
        out = tmp_path / cfg["name"]
        files = coregen.write_session(cfg, out)
        assert set(files) >= {"session.json", "gate_endpoints.ini", "gate_points.ini",
                              "core_session.py", "run_devices.sh"}
        s = json.loads((out / "session.json").read_text())
        # every field device has a startup command and appears in the build script
        cmds = coregen.service_commands(cfg)
        assert set(cmds) == {d["id"] for d in cfg["devices"]}
        script = (out / "core_session.py").read_text()
        for n in s["nodes"]:
            assert repr(n["id"]) in script
        # run script launches one device runner per field device
        runsh = (out / "run_devices.sh").read_text()
        assert all(f"--node {d['id']}" in runsh for d in cfg["devices"])
    # the switched config really carries a switch node and >2 field nodes
    sw = coregen.session(_cfg_switched_many())
    assert any(n["kind"] == "switch" for n in sw["nodes"])
    assert sum(n["role"] == "field-device" for n in sw["nodes"]) == 3


def test_generator_validation_catches_addressing_and_links():
    dup_ip = _cfg_single_mtu()
    dup_ip["devices"][0]["ip"] = "10.0.0.254"            # clashes with MTU
    assert any("IP" in i for i in coregen.validate(dup_ip))

    bad_link = _cfg_single_mtu()
    bad_link["links"].append({"a": "PLC1", "b": "GHOST"})
    assert any("unknown endpoint" in i for i in coregen.validate(bad_link))

    dup_bc = _cfg_local_pair()
    dup_bc["devices"][1]["backchannel"] = dup_bc["devices"][0]["backchannel"]
    assert any("back-channel" in i for i in coregen.validate(dup_bc))


# ----------------------------------------------- communication-layer disturbance
def test_disturbance_measured_at_communication_layer():
    point = DevicePoint("s", ig.MEASUREMENT, is_bool=False)

    async def main():
        dev = Device("PLC1", [point])
        dev.set_input("s", 2.0)
        server = await ModbusServer(dev).serve()
        sport = _port(server)
        try:
            base = await measure_reads("127.0.0.1", sport, dev.by_id["s"], n=20, timeout=0.3)
            # inject delay + 50% loss via a loopback proxy (stand-in for CORE netem)
            proxy = DegradedProxy("127.0.0.1", sport, delay_s=0.02, loss=0.5, seed=1)
            pport = await proxy.start()
            deg = await measure_reads("127.0.0.1", pport, dev.by_id["s"], n=20, timeout=0.3)
            await proxy.stop()
        finally:
            server.close()
        b, d = base.summary(), deg.summary()
        # baseline is clean; the disturbance shows up on the COMMUNICATION layer
        assert b["ok"] == 20 and b["failed"] == 0
        assert d["failed"] > 0                              # dropped transactions
        assert d["fail_rate"] > b["fail_rate"]
        # (we deliberately do NOT assert any physical-process deviation)
        return b, d
    _run(main())
