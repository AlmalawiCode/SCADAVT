"""The three SCADA programs of the networked mode, run together on localhost."""

import asyncio
import csv
import datetime as dt
from pathlib import Path

from scadavt import scenario as sc
from scadavt.deploy.conformance import run as conformance
from scadavt.field.plc import FieldDevice
from scadavt.gateway.bridge import IOModuleGate, MetaConverter
from scadavt.master.mtu import MTU
from scadavt.wds.client import WDSClient
from scadavt.wds.model import Plant
from scadavt.wds.server import WDSServer


def test_field_device_conformance():
    async def go():
        spec = sc.load().plcs["PLC6"]
        dev = FieldDevice(spec)
        s1 = await asyncio.start_server(dev.bank.serve, "127.0.0.1", 0)
        s2 = await asyncio.start_server(dev.serve_io, "127.0.0.1", 0)
        res = await conformance(spec, ("127.0.0.1", s1.sockets[0].getsockname()[1]),
                                ("127.0.0.1", s2.sockets[0].getsockname()[1]))
        s1.close()
        s2.close()
        return res
    res = asyncio.run(go())
    assert res and all(ok for _, ok, _ in res), [r for r in res if not r[1]]


def test_closed_loop_on_localhost(tmp_path: Path):
    """Plant -> gateway -> PLCs -> MTU -> PLCs -> gateway -> plant, 3 simulated hours."""
    async def go():
        scen = sc.load()
        servers, plcs_ini, mtu_plcs = [], ["[DEFAULT]"], {}
        for name, spec in scen.plcs.items():
            dev = FieldDevice(spec)
            s1 = await asyncio.start_server(dev.bank.serve, "127.0.0.1", 0)
            s2 = await asyncio.start_server(dev.serve_io, "127.0.0.1", 0)
            servers += [s1, s2]
            mtu_plcs[name] = {"ip": "127.0.0.1", "port": s1.sockets[0].getsockname()[1]}
            plcs_ini.append(f"[{name}]\nip = 127.0.0.1\nport = {s2.sockets[0].getsockname()[1]}")
        (tmp_path / "plcs.ini").write_text("\n".join(plcs_ini) + "\n")
        tags = []
        from scadavt.protocols import wdsproto as wp
        for t in scen.all_tags():
            tags.append(f"[{t.name}]\ncontroller = {t.plc}\nparaType = {t.direction}\n"
                        f"element = {t.element}\n"
                        f"elementType = {'node' if t.ptype == wp.PT_NODE else 'link'}\n"
                        f"dataType = {wp.DATA_TYPE_NAMES[t.dtype]}")
        (tmp_path / "tags.ini").write_text("\n".join(tags) + "\n")

        plant = Plant(scen, start=dt.date(2025, 1, 23), seed=1)
        srv = WDSServer(plant, step=300, tick=0.2, hours=3.0, log_path=tmp_path / "gt.csv")
        wserver = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
        wport = wserver.sockets[0].getsockname()[1]
        gate = IOModuleGate(tmp_path / "plcs.ini", tmp_path / "tags.ini")
        mc = MetaConverter(gate, WDSClient("127.0.0.1", wport), 0.2, tmp_path / "gw.csv")
        mtu = MTU(scen, mtu_plcs, 0.2, 5.0, True, tmp_path / "hist.csv", tmp_path / "conn.csv")
        tasks = [asyncio.create_task(x) for x in (srv.clock(), mc.run(), mtu.run())]
        await srv.done.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await gate.close()
        for s in servers + [wserver]:
            s.close()
        plant.close()
        return scen

    scen = asyncio.run(go())
    hist = list(csv.DictReader(open(tmp_path / "hist.csv")))
    conn = list(csv.DictReader(open(tmp_path / "conn.csv")))
    gt = list(csv.DictReader(open(tmp_path / "gt.csv")))
    gw = list(csv.DictReader(open(tmp_path / "gw.csv")))
    last = max(int(r["cycle"]) for r in conn)  # the last cycle is cut by the test
    assert len(hist) >= 10
    busy = [r for r in conn if r["ok"] == "0" and int(r["cycle"]) < last]
    assert all("exception code 6" in r["error"] for r in busy)  # before the first gateway cycle
    # the MTU sees the plant's tank level through the PLCs
    v1 = scen.control["v1"]
    col = f"{v1['t2'][0]}.{v1['t2'][1]}"
    assert all(0.0 < float(r[col]) < 10.0 for r in hist[3:])
    # the V1 setpoint chosen by the MTU reaches the plant
    last_cmd = float(hist[-3][f"cmd.{v1['plc']}.{v1['setpoint_tag']}"])
    setting_col = [c for c in gt[0] if c.startswith("V1_") and "setting" in c][0]
    assert abs(float(gt[-1][setting_col]) - last_cmd) / max(last_cmd, 1.0) < 0.25
    assert sum(int(r["n_controls"]) for r in gw) > 0
