"""A guided, runnable tour of the SCADAVT domain-neutral core — no CORE needed.

    python -m scadavt.examples.demo

It (A) generates a CORE session from a plain config, (B) runs a live SCADA
control loop (MTU reads a PLC over Modbus, a rule decides, it commands an
actuator), (C) swaps in the water/EPANET app with no change to the core, and
(D) exports a labelled experiment bundle. Everything runs over loopback; on a
CORE host the same config runs across emulated nodes/links with pcap capture
(see docs/CORE_RUNBOOK.md).
"""

from __future__ import annotations

import asyncio
import tempfile

from ..core import coregen
from ..core import integration as ig
from ..core import labels as lbl
from ..core.bundle import ExperimentRecorder
from ..core.control import ControlHost
from ..core.fieldbus import Device, DevicePoint, ModbusClientPlugin, ModbusServer
from ..core.timing import Binding, LocalTransport, NetworkTransport, Runner
from .dummy_app import DummyProcess


def _hr(t: str) -> None:
    print("\n" + "=" * 64 + f"\n  {t}\n" + "=" * 64)


def demo_config() -> dict:
    """A tiny, domain-neutral lab: one PLC + MTU, one measurement, one command."""
    return {
        "name": "demo", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC1", "protocol": "modbus", "ip": "10.0.0.1",
                     "backchannel": "172.16.0.1:9161",
                     "points": [{"id": "x_meas", "role": "measurement", "is_bool": False},
                                {"id": "u_cmd", "role": "command", "is_bool": True}]}],
        "links": [{"a": "PLC1", "b": "MTU", "delay_ms": 10, "loss": 0.0}],
        "external_apps": [{"name": "proc", "endpoint": "127.0.0.1:9000"}],
        "mapping": [{"point": "x_meas", "device": "PLC1", "role": "measurement"},
                    {"point": "u_cmd", "device": "PLC1", "role": "command"}],
    }


HYSTERESIS = [
    {"id": "on", "if": [{"kind": "point", "comp": "x", "attr": "value",
                         "op": "below", "value": 3.0}],
     "then": [{"comp": "u", "kind": "pump", "attr": "status", "value": "open"}]},
    {"id": "off", "if": [{"kind": "point", "comp": "x", "attr": "value",
                          "op": "above", "value": 5.0}],
     "then": [{"comp": "u", "kind": "pump", "attr": "status", "value": "closed"}]},
]


def part_a_generate() -> dict:
    _hr("A) Generate a CORE session from a plain config (no domain terms)")
    cfg = demo_config()
    out = tempfile.mkdtemp()
    files = coregen.write_session(cfg, out)
    print("validation:", coregen.validate(cfg) or "OK")
    print("wrote:", ", ".join(sorted(files)))
    print("\nrun_devices.sh:\n" + open(files["run_devices.sh"]).read().strip())
    return files


async def _loop_dummy(cycles: int = 40) -> None:
    app = DummyProcess(x0=1.0)
    app_srv = await app.serve()
    client = ig.IntegrationClient("127.0.0.1", app_srv.sockets[0].getsockname()[1])
    await client.hello()
    dev = Device("PLC1", [DevicePoint("x_meas", ig.MEASUREMENT, is_bool=False),
                          DevicePoint("u_cmd", ig.COMMAND, is_bool=True)])
    mb_srv = await ModbusServer(dev).serve()
    plugin = ModbusClientPlugin("127.0.0.1", mb_srv.sockets[0].getsockname()[1])
    binds = [Binding("x_meas", "PLC1", ig.MEASUREMENT, key=("point", "x", "value")),
             Binding("u_cmd", "PLC1", ig.COMMAND, comp="u")]
    runner = Runner(client, {"PLC1": dev}, ControlHost.from_rules(HYSTERESIS, "mtu"),
                    binds, NetworkTransport({"PLC1": dev}, {"PLC1": plugin}), step_seconds=10.0)
    print("  rule: actuator ON while x<3, OFF while x>5  (x starts at 1.0)")
    for i in range(cycles):
        r = await runner.run_cycle()
        if i % 8 == 0 or i == cycles - 1:
            cmd = next(iter(r.commands.values()), "-")
            print(f"  cycle {i:3d}   x={app.x:6.2f}   cmd={cmd}")
    await plugin.close(); await client.close(); mb_srv.close(); app_srv.close()


def part_b_live_loop() -> None:
    _hr("B) Run a SCADA control loop LIVE (MTU reads PLC over Modbus, decides, commands)")
    asyncio.run(_loop_dummy())


async def _loop_water() -> None:
    from .water_app import WaterApp
    app = WaterApp()
    srv = await app.serve()
    client = ig.IntegrationClient("127.0.0.1", srv.sockets[0].getsockname()[1])
    await client.hello()
    dev = Device("PLC1", [DevicePoint("T1_level", ig.MEASUREMENT, is_bool=False),
                          DevicePoint("P1_cmd", ig.COMMAND, is_bool=True)])
    binds = [Binding("T1_level", "PLC1", ig.MEASUREMENT, key=("tank", "T1", "level")),
             Binding("P1_cmd", "PLC1", ig.COMMAND, comp="P1")]
    rules = [{"id": "fill", "if": [{"kind": "tank", "comp": "T1", "attr": "level",
                                    "op": "below", "value": 2.5}],
              "then": [{"comp": "P1", "kind": "pump", "attr": "status", "value": "open"}]}]
    runner = Runner(client, {"PLC1": dev}, ControlHost.from_rules(rules, "mtu"),
                    binds, LocalTransport({"PLC1": dev}), step_seconds=300.0)
    print(f"  start tank level = {app.acquire('T1_level', ig.MEASUREMENT):.3f} m")
    await runner.run(12)
    print(f"  end   tank level = {app.acquire('T1_level', ig.MEASUREMENT):.3f} m  (rule filled it)")
    await client.close(); srv.close(); app.close()


def part_c_water() -> None:
    _hr("C) Same core, a different external app: WATER (EPANET) filling a tank")
    try:
        asyncio.run(_loop_water())
    except ModuleNotFoundError:
        print("  (epanet not importable here — skipped)")


def part_d_bundle() -> dict:
    _hr("D) Export a research dataset bundle (trace + 2-D labels + metadata)")
    rec = ExperimentRecorder(metadata={"name": "demo-exp", "placement": "mtu",
                                       "timing": "event-stepped"})
    for i in range(10):
        rec.record(float(i), {"x_meas": 1.0 + 0.3 * i, "u_cmd": 1.0})
    rec.windows = [lbl.AttackWindow("dos", "PLC1", start=3.0, end=7.0)]
    rec.confirm(4.0, "src=attacker")
    rec.confirm(5.0, "altered-cmd")
    out = rec.write_bundle(tempfile.mkdtemp() + "/bundle")
    print("  bundle files:", ", ".join(out["metadata"]["files"]))
    print("  label summary:", out["metadata"]["label_summary"])
    print("  -> attack_window marks 4 samples; only 2 are confirmed-malicious.")
    return out


def main() -> None:
    part_a_generate()
    part_b_live_loop()
    part_c_water()
    part_d_bundle()
    print("\nAll of this ran WITHOUT CORE (loopback). On a CORE host the same config")
    print("runs across emulated nodes and links with pcap capture — see docs/CORE_RUNBOOK.md.")


if __name__ == "__main__":
    main()
