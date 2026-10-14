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
        # every field device has a startup command (and the MTU, when centralized)
        cmds = coregen.service_commands(cfg)
        assert {d["id"] for d in cfg["devices"]} <= set(cmds)
        if cfg.get("mtu") and cfg.get("control", {}).get("placement", "mtu") == "mtu":
            assert cfg["mtu"]["id"] in cmds              # MTU runs the master on-node
        script = (out / "core_session.py").read_text()
        for n in s["nodes"]:
            assert repr(n["id"]) in script
        # the build script assigns each addressable node a real interface IP and
        # launches each program inside its namespace (not just comments)
        for n in s["nodes"]:
            if n.get("ip"):
                assert f"ip4={n['ip']!r}" in script
            if n["id"] in cmds:
                assert f"node_command(session.id, nodes[{n['id']!r}].id" in script
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


# ------------------------------------------------- explicit Modbus register map
def test_register_map_role_fixes_table_and_ref():
    """A PLC's register map is explicit: role fixes the Modbus table (sensor ->
    input register / discrete input; command -> holding register / coil) and the
    5-digit reference follows the convention."""
    dev = Device("PLC1", [
        DevicePoint("level", ig.MEASUREMENT, is_bool=False),
        DevicePoint("run", ig.STATE, is_bool=True),
        DevicePoint("pump", ig.COMMAND, is_bool=True),
        DevicePoint("setpoint", ig.COMMAND, is_bool=False),
    ])
    m = {r["point"]: r for r in dev.register_map()}
    assert m["level"]["table"] == "input_register" and m["level"]["ref"] == 30001
    assert m["level"]["data_type"] == "float32" and m["level"]["access"] == "read-only"
    assert m["run"]["table"] == "discrete_input" and m["run"]["ref"] == 10001
    assert m["pump"]["table"] == "coil" and m["pump"]["ref"] == 1
    assert m["pump"]["access"] == "read/write"
    assert m["setpoint"]["table"] == "holding_register" and m["setpoint"]["ref"] == 40001


def test_register_address_can_be_pinned_in_config():
    """A configuration can pin a point's address; auto-assigned points pack in
    after the pinned range without clashing."""
    dev = Device("PLC1", [
        DevicePoint("level", ig.MEASUREMENT, is_bool=False),                  # auto
        DevicePoint("flow", ig.MEASUREMENT, is_bool=False, address=10, pinned=True),
    ])
    m = {r["point"]: r for r in dev.register_map()}
    assert m["flow"]["address"] == 10 and m["flow"]["ref"] == 30011     # pinned
    assert m["level"]["address"] == 12                                  # packed after
    # addresses never collide
    assert m["level"]["address"] != m["flow"]["address"]


def test_coregen_register_maps_bind_element_to_register():
    """The config-level view ties each register to the physical element it reads
    or drives (the real-PLC configuration the researcher works against)."""
    cfg = {
        "name": "t", "control": {"placement": "mtu"}, "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC2", "protocol": "modbus", "ip": "10.0.0.2",
                     "points": [{"id": "level", "role": "measurement", "is_bool": False},
                                {"id": "pump", "role": "command", "is_bool": True}]}],
        "mapping": [{"point": "level", "device": "PLC2", "role": "measurement", "element": "T2"},
                    {"point": "pump", "device": "PLC2", "role": "command", "element": "P4"}],
    }
    assert coregen.validate(cfg) == []
    rm = coregen.register_maps(cfg)["PLC2"]
    by_point = {r["point"]: r for r in rm}
    assert by_point["level"]["element"] == "T2" and by_point["level"]["table"] == "input_register"
    assert by_point["pump"]["element"] == "P4" and by_point["pump"]["table"] == "coil"


# ------------------------------------------------- CORE topology: IPs, LAN, WAN
def test_build_script_assigns_real_ips_and_switch_lan():
    """A switched config generates a valid CORE script with explicit per-interface
    IPs, a SWITCH node, and bare (IP-less) switch interfaces — CORE's own LAN
    pattern — not IPs hidden in comments."""
    import ast
    cfg = _cfg_switched_many()
    cfg["mtu"]["ip"] = "10.0.0.254"
    for i, d in enumerate(cfg["devices"], 1):
        d["ip"] = f"10.0.0.{i}"
    assert coregen.validate(cfg) == []
    script = coregen.core_build_script(cfg)
    ast.parse(script)                                    # valid Python
    assert "NodeType.SWITCH" in script
    assert "ip4='10.0.0.1'" in script and "ip4_mask=24" in script
    assert "wrappers.Interface(0)" in script             # a bare switch-side interface


def test_build_script_router_makes_wan_with_netem():
    """Two subnets joined by a model='router' node across an impaired link = WAN."""
    import ast
    cfg = {"name": "wan", "control": {"placement": "mtu"}, "netmask": 24,
           "mtu": {"id": "MTU", "ip": "10.0.0.254"},
           "devices": [{"id": "PLC1", "ip": "10.0.0.1",
                        "points": [{"id": "lvl", "role": "measurement", "is_bool": False}]},
                       {"id": "PLC2", "ip": "10.0.1.1",
                        "points": [{"id": "lvl", "role": "measurement", "is_bool": False}]}],
           "switches": [{"id": "SW_A"}, {"id": "SW_B"}], "routers": [{"id": "R1"}],
           "links": [{"a": "PLC1", "b": "SW_A"}, {"a": "MTU", "b": "SW_A"},
                     {"a": "PLC2", "b": "SW_B"},
                     {"a": "R1", "b": "SW_A", "a_ip": "10.0.0.250"},
                     {"a": "R1", "b": "SW_B", "a_ip": "10.0.1.250",
                      "delay_ms": 20, "loss": 0.5, "bw": 10_000_000}]}
    assert coregen.validate(cfg) == []
    script = coregen.core_build_script(cfg)
    ast.parse(script)
    assert "model='router'" in script
    assert "ip4='10.0.0.250'" in script and "ip4='10.0.1.250'" in script  # a leg per subnet
    assert "LinkOptions(delay=20000, loss=0.5, bandwidth=10000000)" in script


def test_router_without_ip_is_rejected():
    cfg = {"name": "w", "control": {"placement": "plc"},
           "devices": [{"id": "PLC1", "ip": "10.0.0.1",
                        "points": [{"id": "x", "role": "measurement", "is_bool": False}]}],
           "switches": [{"id": "SW"}], "routers": [{"id": "R1"}],
           "links": [{"a": "PLC1", "b": "SW"}, {"a": "R1", "b": "SW"}]}  # R1 has no IP
    assert any("router" in i and "needs an IP" in i for i in coregen.validate(cfg))


def test_master_node_drives_device_over_modbus():
    """The on-node MTU master reads a device's measurement register over Modbus,
    runs the rule engine, and writes the command register back — node to node."""
    from scadavt.core.device import run_node
    from scadavt.core.master import run_master

    async def main():
        node = {"id": "PLC1", "role": "field-device", "protocol": "modbus", "ip": "127.0.0.1",
                "points": [{"id": "lvl", "role": "measurement", "is_bool": False},
                           {"id": "pmp", "role": "command", "is_bool": True}]}
        dev, fb_srv, io_srv = await run_node(node, ("127.0.0.1", 0), ("127.0.0.1", 0))
        port = _port(fb_srv)
        dev.set_input("lvl", 2.0)                        # low -> rule opens pump
        session = {"nodes": [dict(node, port=port)],
                   "mapping": [{"point": "lvl", "device": "PLC1", "role": "measurement", "element": "T1"},
                               {"point": "pmp", "device": "PLC1", "role": "command", "element": "P1"}],
                   "rules": [{"id": "on",
                              "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                                      "op": "below", "value": 3.0}],
                              "then": [{"comp": "P1", "kind": "pump", "attr": "status",
                                        "value": "open"}]}]}
        try:
            await run_master(session, step_seconds=0.0, cycles=3)
            return dev.get_output("pmp")
        finally:
            fb_srv.close(); io_srv.close()
    assert _run(main()) == 1.0


# ------------------------------------------------- one-shot Modbus probe + seed
def test_probe_reads_and_writes_points_over_modbus(tmp_path):
    """The probe tool (used by the live CORE test) reads a seeded measurement and
    writes/reads back a command point over real Modbus, and fails (nonzero) when
    the target is unreachable."""
    import argparse
    from scadavt.core.device import run_node
    from scadavt.core import probe

    def ns(**kw):
        d = dict(session=None, node="PLC1", point="lvl", write=None,
                 host=None, port=None, timeout=2.0)
        d.update(kw)
        return argparse.Namespace(**d)

    async def main():
        node = {"id": "PLC1", "role": "field-device", "protocol": "modbus", "ip": "127.0.0.1",
                "points": [{"id": "lvl", "role": "measurement", "is_bool": False},
                           {"id": "pmp", "role": "command", "is_bool": True}]}
        dev, fb_srv, io_srv = await run_node(node, ("127.0.0.1", 0), ("127.0.0.1", 0))
        port = _port(fb_srv)
        dev.set_input("lvl", 4.2)                        # stand-in sensor (what --seed does)
        p = tmp_path / "session.json"
        p.write_text(json.dumps({"nodes": [dict(node, port=port)]}))
        try:
            r_read = await probe._run(ns(session=str(p), point="lvl"))
            r_write = await probe._run(ns(session=str(p), point="pmp", write="1"))
            r_back = await probe._run(ns(session=str(p), point="pmp"))
            r_bad = await probe._run(ns(session=str(p), point="lvl", host="127.0.0.1",
                                        port=1, timeout=0.3))
            return r_read, r_write, r_back, r_bad, dev.get_output("pmp")
        finally:
            fb_srv.close(); io_srv.close()
    r_read, r_write, r_back, r_bad, pump = _run(main())
    assert r_read == 0 and r_write == 0 and r_back == 0      # real reads/writes succeed
    assert r_bad != 0                                        # unreachable -> nonzero
    assert pump == 1.0                                       # the write landed
