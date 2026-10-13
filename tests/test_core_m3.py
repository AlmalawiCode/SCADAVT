"""M3 tests: pluggable strategies (incl. the water Supervisor as an example),
a second field-bus plugin, the standalone domain-neutral gateway, distributed
(PLC-peer) control, and the synchronized experiment bundle.
"""

from __future__ import annotations

import asyncio
import json

from scadavt.core import coregen
from scadavt.core import integration as ig
from scadavt.core import labels as lbl
from scadavt.core.bundle import ExperimentRecorder
from scadavt.core.capture import PcapCapture
from scadavt.core.control import ControlHost, dict_reader
from scadavt.core.device import run_node
from scadavt.core.fieldbus import (Device, DevicePoint, ModbusClientPlugin, ModbusServer,
                                   SimpleTcpClient, SimpleTcpServer)
from scadavt.core.gateway import Gateway
from scadavt.core.strategy import RuleStrategy
from scadavt.examples.dummy_app import DummyProcess
from scadavt.examples.water_strategy import WaterStrategy


def _run(coro):
    return asyncio.run(coro)


def _port(server):
    return server.sockets[0].getsockname()[1]


HYST = [
    {"id": "on", "if": [{"kind": "point", "comp": "x", "attr": "value",
                         "op": "below", "value": 2.0}],
     "then": [{"comp": "u", "kind": "pump", "attr": "status", "value": "open"}]},
]


# ----------------------------------------------------------------- strategies
def test_rule_strategy_matches_rule_engine():
    values = {("point", "x", "value"): 1.5}
    via_rules = ControlHost.from_rules(HYST, "plc").decide(dict_reader(values), 0)
    via_strat = ControlHost.from_strategy(RuleStrategy.from_rules(HYST), "plc") \
        .decide(dict_reader(values), 0)
    assert via_rules == via_strat and via_strat[0]["comp"] == "u"


def test_water_supervisor_as_strategy():
    """The thesis Supervisor runs behind the generic strategy interface."""
    cfg = {"pump_groups": [{"name": "g1", "plc": "PLC1", "tank": "T1",
                            "tank_tag": ["PLC1", "T1_LVL"], "pumps": ["P1"],
                            "on_below": 0.4, "off_above": 0.9}],
           "v1": {"plc": "PLC1", "setpoint_tag": "V1_SET", "fixed_setting": 5.0,
                  "t2": ["PLC1", "t2"], "t3": ["PLC1", "t3"], "b": ["PLC1", "b"],
                  "c": ["PLC1", "c"], "f": ["PLC1", "f"]}}
    bindings = {("PLC1", "T1_LVL"): ("tank", "T1", "level")}
    strat = WaterStrategy(cfg, {"T1": 5.0}, bindings, v1_control=False)
    host = ControlHost.from_strategy(strat, "mtu")
    # tank at 1.0 of max 5.0 -> frac 0.2 < 0.4 -> pump on
    cmds = host.decide(dict_reader({("tank", "T1", "level"): 1.0}), 0)
    by_comp = {c["comp"]: c["value"] for c in cmds}
    assert by_comp["P1_ST_O"] == 1.0 and by_comp["V1_SET"] == 5.0


# --------------------------------------------------------- second field-bus plugin
def test_simpletcp_plugin_roundtrip():
    async def main():
        dev = Device("PLC1", [DevicePoint("s", ig.MEASUREMENT, is_bool=False),
                              DevicePoint("a", ig.COMMAND, is_bool=True)])
        server = await SimpleTcpServer(dev).serve()
        client = SimpleTcpClient("127.0.0.1", _port(server))
        try:
            dev.set_input("s", 4.25)
            assert abs(await client.read(dev.by_id["s"]) - 4.25) < 1e-4
            await client.write(dev.by_id["a"], 1.0)
            assert dev.get_output("a") == 1.0
        finally:
            await client.close()
            server.close()
    _run(main())


def test_coregen_accepts_mixed_protocols():
    cfg = {"name": "mixed", "control": {"placement": "plc"},
           "devices": [
               {"id": "PLC1", "protocol": "modbus", "ip": "10.0.0.1",
                "backchannel": "172.16.0.1:9161",
                "points": [{"id": "s1", "role": "measurement", "is_bool": False}]},
               {"id": "PLC2", "protocol": "simpletcp", "ip": "10.0.0.2",
                "backchannel": "172.16.0.2:9161",
                "points": [{"id": "s2", "role": "measurement", "is_bool": False}]}],
           "links": [{"a": "PLC1", "b": "PLC2"}],
           "external_apps": [{"name": "proc", "endpoint": "127.0.0.1:9000"}],
           "mapping": [{"point": "s1", "device": "PLC1", "role": "measurement"},
                       {"point": "s2", "device": "PLC2", "role": "measurement"}]}
    assert coregen.validate(cfg) == []
    cmds = coregen.service_commands(cfg)
    assert "--modbus 10.0.0.1:502" in cmds["PLC1"]       # modbus default port
    assert "--modbus 10.0.0.2:7002" in cmds["PLC2"]      # simpletcp default port


# ----------------------------------------------------- standalone gateway (M3)
def test_standalone_gateway_moves_io_over_backchannel():
    node = {"id": "PLC1", "protocol": "modbus", "backchannel": None, "points": [
        {"id": "x_meas", "role": ig.MEASUREMENT, "is_bool": False},
        {"id": "u_state", "role": ig.STATE, "is_bool": True},
        {"id": "u_cmd", "role": ig.COMMAND, "is_bool": True}]}

    async def main():
        device, fb_server, io_server = await run_node(node, ("127.0.0.1", 0), ("127.0.0.1", 0))
        mb_port, io_port = _port(fb_server), _port(io_server)
        node["backchannel"] = f"127.0.0.1:{io_port}"
        session = {"nodes": [node], "mapping": [
            {"point": "x_meas", "device": "PLC1", "role": ig.MEASUREMENT},
            {"point": "u_state", "device": "PLC1", "role": ig.STATE},
            {"point": "u_cmd", "device": "PLC1", "role": ig.COMMAND}]}
        app = DummyProcess(x0=3.0)
        app_server = await app.serve()
        client = ig.IntegrationClient("127.0.0.1", _port(app_server))
        await client.hello()
        gw = Gateway.from_session(session, client)
        master = ModbusClientPlugin("127.0.0.1", mb_port)   # stands in for the MTU
        try:
            # a controller (MTU) commands the actuator over the SCADA net
            await master.write(device.by_id["u_cmd"], 1.0)
            await gw.run(2)
            # measurement flowed app -> device (readable over Modbus)
            assert abs(await master.read(device.by_id["x_meas"]) - 3.0) < 1e-4
            # command flowed device -> external app (gateway applied it)
            assert app.u == 1.0
        finally:
            await master.close()
            await gw.close()
            fb_server.close()
            io_server.close()
            app_server.close()
    _run(main())


# ------------------------------------------------------ distributed (PLC-peer)
def test_distributed_local_control_with_peer_read():
    """Device A runs local control that reads a neighbour's measurement over the
    SCADA net and actuates its own output — no MTU (placement='distributed')."""
    async def main():
        a = Device("A", [DevicePoint("ma", ig.MEASUREMENT, is_bool=False),
                         DevicePoint("ca", ig.COMMAND, is_bool=True)])
        b = Device("B", [DevicePoint("mb", ig.MEASUREMENT, is_bool=False)])
        b.set_input("mb", 1.0)                         # neighbour reports a low value
        b_server = await ModbusServer(b).serve()
        peer = ModbusClientPlugin("127.0.0.1", _port(b_server))
        rules = [{"id": "peer", "if": [{"kind": "point", "comp": "nb", "attr": "value",
                                        "op": "below", "value": 2.0}],
                  "then": [{"comp": "ca", "kind": "pump", "attr": "status", "value": "open"}]}]
        host = ControlHost.from_rules(rules, "distributed")
        try:
            assert host.needs_mtu is False
            peer_val = await peer.read(b.by_id["mb"])   # peer-to-peer read over the net
            values = {("point", "nb", "value"): peer_val}
            for cmd in host.decide(dict_reader(values), 0):
                a.set_output(cmd["comp"], cmd["value"])  # local actuation, no MTU
            assert a.get_output("ca") == 1.0
        finally:
            await peer.close()
            b_server.close()
    _run(main())


# ----------------------------------------------------------- experiment bundle
def test_experiment_bundle(tmp_path):
    rec = ExperimentRecorder(metadata={"name": "exp1", "placement": "mtu",
                                        "timing": "event-stepped", "seed": 1})
    for i in range(10):
        rec.record(float(i), {"T1_level": 2.0 + 0.1 * i, "P1_cmd": 1.0})
    rec.windows = [lbl.AttackWindow("dos", "PLC1", start=3.0, end=7.0)]
    rec.confirm(4.0, "src=attacker")
    rec.confirm(5.0, "altered-cmd")
    fake_pcap = tmp_path / "plc1.pcap"
    fake_pcap.write_bytes(b"\xd4\xc3\xb2\xa1")           # pcap magic, stand-in capture
    rec.pcaps = [str(fake_pcap)]

    out = rec.write_bundle(tmp_path / "bundle")
    meta = out["metadata"]
    assert meta["samples"] == 10
    assert meta["label_summary"]["in_window"] == 4
    assert meta["label_summary"]["confirmed_malicious"] == 2
    assert meta["label_summary"]["benign_in_window"] == 2   # window != malicious
    assert "plc1.pcap" in meta["pcaps"]
    # files written and both label dimensions present
    import csv
    from pathlib import Path
    rows = list(csv.DictReader(open(Path(out["dir"]) / "labels.csv")))
    assert {"attack_window", "malicious_confirmed"} <= set(rows[0])
    assert sum(int(r["attack_window"]) for r in rows) == 4
    assert sum(int(r["malicious_confirmed"]) for r in rows) == 2
    assert (Path(out["dir"]) / "trace.csv").exists()
    assert json.loads((Path(out["dir"]) / "metadata.json").read_text())["name"] == "exp1"


def test_capture_wrapper_constructs():
    cap = PcapCapture("/tmp/x.pcap", iface="any", bpf="tcp port 502")
    assert isinstance(cap.available(), bool)             # tcpdump present or not
    assert cap.proc is None                              # not started (needs privileges)
