#!/usr/bin/env python3
"""Live proof of PLC I/O modules + emulated sensors/actuators on real CORE/Modbus.

Two PLCs with DI/DO/AI modules. Emulated sensors (temperature, humidity,
switch, pressure) are wired to input channels and an emulated fan to an output
channel; the MTU reads the channels over real Modbus and its rules drive
outputs across PLCs:

    PLC1.AI0 (TT101 temperature = 28)  > 25  =>  PLC2.DO0 (FAN1) on
    PLC2.AI0 (PT201 pressure = 2.5)    < 3   =>  PLC1.DO1 on

    SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python \\
        /opt/core/venv/bin/python tools/live_core_iomodules.py [--wan]

--wan puts PLC2 on a second subnet behind a router. Needs a running
core-daemon. Exit 0 only if all checks pass.
"""

from __future__ import annotations

import os
import struct
import sys
import time

REPO = os.environ.get("SCADAVT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.api.grpc import client  # noqa: E402
from scadavt.core import corelive  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def config(wan: bool) -> dict:
    plc2_ip = "10.0.2.1" if wan else "10.0.1.2"
    cfg = {
        "schema_version": 1, "name": "iolab", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.1.254"},
        "devices": [
            {"id": "PLC1", "ip": "10.0.1.1", "protocol": "modbus",
             "io_modules": [{"type": "AI", "channels": 4}, {"type": "DI", "channels": 2},
                            {"type": "DO", "channels": 4}]},
            {"id": "PLC2", "ip": plc2_ip, "protocol": "modbus",
             "io_modules": [{"type": "AI", "channels": 2}, {"type": "DO", "channels": 2}]}],
        "switches": [{"id": "SW1"}],
        "links": [{"a": "MTU", "b": "SW1", "a_ip": "10.0.1.254"},
                  {"a": "PLC1", "b": "SW1", "a_ip": "10.0.1.1"}],
        "sensors": [
            {"id": "TT101", "type": "temperature", "device": "PLC1", "channel": "AI0",
             "wave": "constant", "value": 28.0},
            {"id": "HT101", "type": "humidity", "device": "PLC1", "channel": "AI1"},
            {"id": "SW101", "type": "switch", "device": "PLC1", "channel": "DI0",
             "wave": "constant", "value": 1.0},
            {"id": "PT201", "type": "pressure", "device": "PLC2", "channel": "AI0",
             "wave": "constant", "value": 2.5}],
        "actuators": [{"id": "FAN1", "type": "fan", "device": "PLC2", "channel": "DO0"}],
        "logic": [
            {"id": "fan_on", "if": [{"device": "PLC1", "register": "AI0", "op": "above", "value": 25}],
             "then": [{"device": "PLC2", "register": "DO0", "set": 1}]},
            {"id": "fan_off", "if": [{"device": "PLC1", "register": "AI0", "op": "below", "value": 24}],
             "then": [{"device": "PLC2", "register": "DO0", "set": 0}]},
            {"id": "low_p", "if": [{"device": "PLC2", "register": "AI0", "op": "below", "value": 3}],
             "then": [{"device": "PLC1", "register": "DO1", "set": 1}]}],
    }
    if wan:
        cfg["switches"].append({"id": "SW2"})
        cfg["routers"] = [{"id": "R1", "model": "router"}]
        cfg["links"] += [{"a": "R1", "b": "SW1", "a_ip": "10.0.1.250"},
                         {"a": "R1", "b": "SW2", "a_ip": "10.0.2.250"},
                         {"a": "PLC2", "b": "SW2", "a_ip": plc2_ip}]
    else:
        cfg["links"].append({"a": "PLC2", "b": "SW1", "a_ip": plc2_ip})
    return cfg


def modbus_fc4_floats(pcap: str, src_ip: str) -> list:
    """Float32 values in Modbus FC4 (read input registers) responses from
    ``src_ip`` in a pcap — pure-Python pcap/Ethernet/IPv4/TCP parsing."""
    vals = []
    with open(pcap, "rb") as fh:
        data = fh.read()
    off = 24                                             # global header
    while off + 16 <= len(data):
        incl = struct.unpack("<I", data[off + 8:off + 12])[0]
        pkt = data[off + 16:off + 16 + incl]
        off += 16 + incl
        if len(pkt) < 34 or pkt[12:14] != b"\x08\x00":
            continue
        ip = pkt[14:]
        ihl = (ip[0] & 0x0F) * 4
        if ip[9] != 6 or ".".join(map(str, ip[12:16])) != src_ip:
            continue
        tcp = ip[ihl:]
        if struct.unpack(">H", tcp[0:2])[0] != 502:
            continue
        pay = tcp[(tcp[12] >> 4) * 4:]
        # MBAP(7) + FC + byte count + registers
        if len(pay) >= 13 and pay[7] == 0x04 and pay[8] == 4:
            vals.append(struct.unpack(">f", pay[9:13])[0])
    return vals


def main() -> int:
    wan = "--wan" in sys.argv
    print(f"\n== PLC I/O modules + emulated sensors on live CORE ({'WAN' if wan else 'LAN'}) ==")
    core = client.CoreGrpcClient(); core.connect()
    net = corelive.build(core, config(wan))
    try:
        net.start(cycles=200)
        time.sleep(4.0)
        check("MTU reaches PLC1", net.ping("MTU", "10.0.1.1"))
        check(f"MTU reaches PLC2{' across the router' if wan else ''}",
              net.ping("MTU", config(wan)["devices"][1]["ip"]))
        p1 = net.probe_many("MTU", "PLC1", ["AI0", "AI1", "DI0", "DO0", "DO1"])
        p2 = net.probe_many("MTU", "PLC2", ["AI0", "DO0", "DO1"])
        check("temperature sensor TT101 -> PLC1.AI0 (input register 30001) read over Modbus",
              p1["AI0"] is not None and abs(p1["AI0"] - 28.0) < 0.01, f"AI0={p1['AI0']}")
        check("humidity sensor HT101 -> PLC1.AI1 in its 35..65 %RH range",
              p1["AI1"] is not None and 35.0 <= p1["AI1"] <= 65.0, f"AI1={p1['AI1']}")
        check("switch SW101 -> PLC1.DI0 (discrete input 10001) = 1", p1["DI0"] == 1.0, f"DI0={p1['DI0']}")
        check("pressure sensor PT201 -> PLC2.AI0 = 2.5",
              p2["AI0"] is not None and abs(p2["AI0"] - 2.5) < 0.01, f"AI0={p2['AI0']}")
        check("rule PLC1.AI0 > 25 => PLC2.DO0 on (cross-PLC, sensor-driven)",
              p2["DO0"] == 1.0, f"PLC2.DO0={p2['DO0']}")
        check("rule PLC2.AI0 < 3 => PLC1.DO1 on", p1["DO1"] == 1.0, f"PLC1.DO1={p1['DO1']}")
        check("untouched outputs stay off (PLC1.DO0, PLC2.DO1)",
              p1["DO0"] == 0.0 and p2["DO1"] == 0.0, f"PLC1.DO0={p1['DO0']} PLC2.DO1={p2['DO1']}")
        _, log = net.nrun("PLC2", "cat /tmp/iohost_PLC2.log")
        check("emulated actuator FAN1 received the MTU's command",
              "actuator FAN1 <- 1.0" in (log or ""),
              next((l.strip() for l in (log or "").splitlines() if "FAN1" in l), "no log line"))
        # a sensor channel is read-only on the network: a master write cannot change it
        net.probe("MTU", "PLC1", "AI0", write=99)
        after = net.probe("MTU", "PLC1", "AI0")
        check("AI channel is read-only over Modbus (write attempt leaves AI0 = 28)",
              after is not None and abs(after - 28.0) < 0.01, f"AI0 after write attempt={after}")
        pcap = os.path.join(net.workdir, "iolab.pcap")
        net.capture("MTU", pcap, seconds=3)
        floats = modbus_fc4_floats(pcap, "10.0.1.1")
        check("the temperature 28.0 is visible inside a Modbus FC4 response in the capture",
              any(abs(v - 28.0) < 0.01 for v in floats), f"{len(floats)} FC4 replies, e.g. {floats[:3]}")
        print(f"   evidence: {pcap}")
    finally:
        net.stop()

    print("\n" + "=" * 64 + f"\n  RESULT   PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    FAIL -> {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
