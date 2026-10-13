"""End-to-end test of the WaterSystem server over TCP on localhost."""

import asyncio
import csv
import datetime as dt

import pytest

from scadavt import scenario as sc
from scadavt.protocols import wdsproto as wp
from scadavt.wds.client import WDSClient, WDSError
from scadavt.wds.model import Plant
from scadavt.wds.server import WDSServer


def test_server_acquire_control_clock(tmp_path):
    async def scenario():
        plant = Plant(sc.load(), start=dt.date(2025, 1, 23), seed=1)
        srv = WDSServer(plant, step=300, tick=0.05, hours=2.0, log_path=tmp_path / "gt.csv")
        server = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        clock = asyncio.create_task(srv.clock())
        c = WDSClient("127.0.0.1", port)
        await c.connect()
        t0 = await c.sim_hours()
        lvl = await c.acquire("T2", wp.PT_NODE, wp.DT_LEVEL)
        assert 0.0 < lvl <= 7.0
        assert await c.control("P4", wp.PT_LINK, wp.DT_STATUS, 1.0) == 1.0
        with pytest.raises(WDSError):
            await c.acquire("NOPE", wp.PT_NODE, wp.DT_LEVEL)
        await srv.done.wait()
        t1 = await c.sim_hours()
        st = await c.acquire("P4", wp.PT_LINK, wp.DT_STATUS)
        await c.close()
        server.close()
        clock.cancel()
        plant.close()
        return t0, t1, st

    t0, t1, st = asyncio.run(scenario())
    assert t0 == 0.0 and t1 == pytest.approx(2.0)
    assert st == 1.0
    rows = list(csv.DictReader(open(tmp_path / "gt.csv")))
    assert len(rows) == 25  # initial state + 24 steps of 5 min
    assert float(rows[-1]["P4_status"]) == 1.0
