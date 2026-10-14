"""Closed-loop SCADA simulation with EPANET as an independent external app.

The physical process (EPANET) is a separate application reached only through the
public Integration API; SCADAVT Core holds no domain knowledge. These tests
exercise the complete cycle over real sockets (loopback) — the same software the
CORE deployment runs across namespaces:

    EPANET app --Integration API--> gateway --IOModules--> device registers
    MTU --Modbus--> device registers --(control)--> device registers
    gateway --Integration API--> EPANET app --STEP--> new measurements

Requires the ``epanet`` library.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scadavt.core import integration as ig
from scadavt.core.device import run_node
from scadavt.core.fieldbus import ModbusClientPlugin
from scadavt.core.gateway_run import run_gateway

pytest.importorskip("epanet")

SCEN = Path(__file__).resolve().parents[1] / "scadavt" / "scenarios" / "closed_loop_min"


def _cfg():
    return json.loads((SCEN / "lab.json").read_text())


def _run(coro):
    return asyncio.run(coro)


def _port(server):
    return server.sockets[0].getsockname()[1]


def test_closed_loop_min_moves_the_tank_over_sockets():
    """The full closed loop over real sockets drives observable physical change:
    the pump, commanded through Modbus, fills/drains the EPANET tank under the
    control rules — the tank level must span a visible range and the pump toggle."""
    from scadavt.labui.netrun import NetworkedRun

    async def main():
        nr = NetworkedRun(_cfg(), step_seconds=120.0)
        return await nr.run(60)
    out = _run(main())
    tr = out["traces"]["PLC1"]
    assert out["engine"] == "networked"
    assert max(tr) - min(tr) > 1.0            # observable physical change (metres)
    assert min(tr) < 4.0 and max(tr) > 5.0    # it actually crossed both thresholds


def test_gateway_runner_bridges_app_and_device():
    """The standalone gateway process pulls a measurement from the EPANET app over
    the Integration API, writes it into the device (back channel), and advances the
    physical clock — and the value is then readable from the device over Modbus."""
    cfg = _cfg()
    from scadavt.examples.design_app import DesignApp

    async def main():
        app = DesignApp(cfg["epanet"], cfg["mapping"])
        app_srv = await app.serve("127.0.0.1", 0)
        node = {"id": "PLC1", "role": "field-device", "protocol": "modbus", "ip": "127.0.0.1",
                "points": cfg["devices"][0]["points"]}
        dev, fb_srv, io_srv = await run_node(node, ("127.0.0.1", 0), ("127.0.0.1", 0))
        session = {"nodes": [dict(node, port=_port(fb_srv),
                                  backchannel=f"127.0.0.1:{_port(io_srv)}")],
                   "mapping": cfg["mapping"]}
        t0 = app.clock()
        try:
            # run the real gateway process logic for a few cycles
            await run_gateway(session, "127.0.0.1", _port(app_srv), step_seconds=120.0, cycles=5)
            # the app advanced (STEP was driven by the gateway)
            stepped = app.clock() - t0
            # the measurement crossed app -> device and is readable over Modbus
            client = ModbusClientPlugin("127.0.0.1", _port(fb_srv))
            over_modbus = await client.read(dev.by_id["T1_LEV"])
            await client.close()
            app_level = app.acquire("T1_LEV", ig.MEASUREMENT)
            return stepped, over_modbus, app_level
        finally:
            fb_srv.close(); io_srv.close(); app_srv.close(); app.close()
    stepped, over_modbus, app_level = _run(main())
    assert stepped >= 5 * 120 - 1             # 5 STEPs of 120 s were applied
    assert 0.0 < over_modbus < 7.0            # a real tank level reached the register over Modbus
    # the register trails the app by at most one hydraulic step (write-then-step ordering)
    assert abs(over_modbus - app_level) < 1.5


def _run_speed(attack=None):
    cfg = json.loads((SCEN / "lab_speed.json").read_text())
    from scadavt.labui.netrun import NetworkedRun

    async def main():
        nr = NetworkedRun(cfg, step_seconds=120.0)
        if attack:
            nr.add_attack(attack, "PLC1", 15, 25)
        return await nr.run(40)
    return _run(main())


def test_analog_pump_speed_control_closed_loop():
    """The PLC commands an analog pump SPEED setpoint (not just on/off), applied to
    EPANET as EN_SETTING; the benign three-way trace agrees end to end."""
    out = _run_speed()
    log = out["actuators"]
    assert any(r["commanded"] == 0.7 for r in log)        # a genuine analog setpoint
    benign = [r for r in log if r["commanded"] is not None]
    assert all(abs(r["commanded"] - r["received"]) < 1e-6 and
               abs(r["received"] - r["applied"]) < 1e-6 for r in benign)  # no divergence benign


def test_three_way_trace_exposes_integrity_tampering():
    """Under an integrity attack the three values diverge: control still COMMANDS a
    speed, but the PLC RECEIVES a tampered 0 and EPANET APPLIES 0 — the evidence a
    cyberattack study needs — with a visible physical consequence (tank drains)."""
    out = _run_speed("integrity")
    log = out["actuators"]
    atk = [r for r in log if r["attack"] == "integrity"]
    assert atk, "attack window produced no records"
    assert all(r["commanded"] > 0 for r in atk)           # control wanted the pump running
    assert all(r["received"] == 0.0 and r["applied"] == 0.0 for r in atk)  # tampered + applied
    # physical consequence: the tank falls well below where the benign run keeps it
    tr = out["traces"]["PLC1"]
    assert min(tr) < 2.0


def test_three_way_trace_dos_freezes_received_command():
    """Under DoS the command never reaches the PLC: commanded changes but received
    stays at the last value it held (stale), so commanded != received in-window."""
    out = _run_speed("dos")
    log = out["actuators"]
    atk = [r for r in log if r["attack"] == "dos"]
    assert atk
    # at least one in-window cycle shows the commanded setpoint not reflected in the register
    assert any(r["received"] != r["commanded"] for r in atk)


def test_epanet_runs_standalone_and_scada_regulates_it():
    """EPANET simulates on its own (no SCADA); without supervisory control the
    tank is unregulated (drains toward empty), whereas the SCADA closed loop keeps
    it in band — the baseline-vs-controlled contrast."""
    from scadavt.examples.epanet_standalone import baseline
    cfg = _cfg()
    base = baseline(cfg["epanet"], cycles=40, step_seconds=120.0)
    base_min = min(r["T1"] for r in base)
    assert len(base) == 40                      # EPANET advanced on its own, no SCADA
    assert base_min < 1.0                        # unregulated: tank nearly empties

    from scadavt.labui.netrun import NetworkedRun
    async def run_ctrl():
        return await NetworkedRun(cfg, step_seconds=120.0).run(40)
    ctrl = asyncio.run(run_ctrl())
    assert min(ctrl["traces"]["PLC1"]) > base_min + 1.0   # supervisory control keeps it up


def test_internal_control_conflict_is_detected():
    """A SCADA-controlled actuator that EPANET also drives internally is flagged
    (so control stays exclusively with the PLCs, per the testbed requirement)."""
    from scadavt.labui.inp import scada_control_conflicts
    clean = (SCEN / "network.inp").read_text()
    assert scada_control_conflicts(clean, ["P1"]) == []          # our scenario is clean
    polluted = clean.replace("[TIMES]",
                             "[CONTROLS]\n LINK P1 OPEN IF NODE T1 BELOW 3\n\n[TIMES]", 1)
    issues = scada_control_conflicts(polluted, ["P1"])
    assert len(issues) == 1 and "P1" in issues[0]


def test_closed_loop_reports_no_control_conflicts_for_clean_scenario():
    """The closed-loop run surfaces the conflict check in its result."""
    from scadavt.labui.netrun import NetworkedRun
    out = asyncio.run(NetworkedRun(_cfg(), step_seconds=120.0).run(5))
    assert out["control_conflicts"] == []


def test_modbus_injection_overwrites_command_register_over_the_wire():
    """The attacker's real Modbus writes overwrite a device command register
    (what runs on the SCADA network in the live CORE experiment)."""
    from scadavt.core.device import run_node
    from scadavt.attacks.modbus_inject import inject

    async def main():
        node = {"id": "PLC1", "role": "field-device", "protocol": "modbus", "ip": "127.0.0.1",
                "points": [{"id": "P1_SPD", "role": "command", "is_bool": False}]}
        dev, fb_srv, io_srv = await run_node(node, ("127.0.0.1", 0), ("127.0.0.1", 0))
        port = _port(fb_srv)
        dev.set_output("P1_SPD", 0.7)                       # legitimate value in the register
        session = {"nodes": [dict(node, port=port)]}
        try:
            n = await inject(session, "PLC1", "P1_SPD", 0.0, hz=100, seconds=0.4,
                             host="127.0.0.1", port=port)
            return n, dev.get_output("P1_SPD")
        finally:
            fb_srv.close(); io_srv.close()
    n, after = _run(main())
    assert n > 0 and after == 0.0                           # attacker forced the register to 0
