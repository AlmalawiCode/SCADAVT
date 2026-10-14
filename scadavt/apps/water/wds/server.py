"""WaterSystem server: the simulated plant, served over TCP with the WDS protocol.

The server owns the simulated clock. Every ``tick`` wall-clock seconds it
advances the hydraulic solution by ``step`` simulated seconds, so the plant
runs ``step / tick`` times faster than real time. Between two steps it answers
WDS requests: acquisitions read the current state, and control requests change
a pump or valve, which takes effect at the next step. After every step it
appends the full plant state (the ground truth) to a CSV file, time-stamped
with both the wall clock and the simulated clock.

    python -m scadavt.wds.server --listen 0.0.0.0:9000 --start 2025-01-23 \
        --step 300 --tick 1.0 --hours 168 --log /logs/ground_truth.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import time
from pathlib import Path

from scadavt import scenario as sc
from scadavt.protocols import wdsproto as wp
from scadavt.util import hostport, setup_logging
from .model import Plant

log = setup_logging("wds")


class WDSServer:
    def __init__(self, plant: Plant, step: int, tick: float, hours: float,
                 log_path: Path | None):
        self.plant, self.step, self.tick, self.hours = plant, step, tick, hours
        self.log_path = log_path
        self.requests = 0
        self.controls = 0
        self.done = asyncio.Event()
        self._writer = None
        self._fh = None

    # ----------------------------------------------------------- requests
    def answer(self, m: wp.WDSMessage) -> wp.WDSMessage:
        self.requests += 1
        net = self.plant.net
        try:
            if m.pid == "SIMCLOCK" and m.function == wp.FC_ACQUIRE:
                return wp.WDSMessage(m.pid, m.function, m.ptype, m.dtype, net.t / 3600.0)
            if not net.has(m.pid, m.ptype):
                raise KeyError(m.pid)
            if m.function == wp.FC_ACQUIRE:
                return wp.WDSMessage(m.pid, m.function, m.ptype, m.dtype,
                                     net.get(m.pid, m.ptype, m.dtype))
            if m.function == wp.FC_CONTROL:
                net.set(m.pid, m.ptype, m.dtype, m.data)
                self.controls += 1
                return wp.WDSMessage(m.pid, m.function, m.ptype, m.dtype, m.data)
            raise ValueError(f"function {m.function}")
        except Exception as e:  # unknown element, wrong type, bad value
            log.debug("request failed: %s %s", m, e)
            return wp.WDSMessage(m.pid, wp.FC_ERROR, m.ptype, m.dtype, 0.0)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        log.info("client %s connected", peer)
        try:
            while True:
                raw = await reader.readexactly(wp.PDU_SIZE)
                writer.write(self.answer(wp.WDSMessage.unpack(raw)).pack())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            log.info("client %s closed", peer)

    # -------------------------------------------------------------- clock
    def _log_row(self, wall: float) -> None:
        if self.log_path is None:
            return
        row = self.plant.net.snapshot()
        row = {"wall_time": round(wall, 3), "sim_h": round(row["sim_s"] / 3600.0, 4), **row}
        if self._writer is None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.log_path, "w", newline="")
            self._writer = csv.DictWriter(self._fh, fieldnames=list(row))
            self._writer.writeheader()
        self._writer.writerow(row)
        self._fh.flush()

    async def clock(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = time.monotonic()
        self._log_row(time.time())
        late = 0
        while self.plant.net.t < self.hours * 3600:
            next_tick += self.tick
            delay = next_tick - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                late += 1
            # the solver runs in a thread so that requests keep being served
            await loop.run_in_executor(None, self.plant.advance, self.step)
            self._log_row(time.time())
        log.info("simulation finished: %.1f h, %d requests, %d controls, %d late ticks",
                 self.plant.net.t / 3600, self.requests, self.controls, late)
        if self._fh:
            self._fh.close()
        self.done.set()


async def main_async(a) -> None:
    scen = sc.load(a.scenario)
    plant = Plant(scen, start=a.start, seed=a.seed, hyd_step=a.step)
    srv = WDSServer(plant, a.step, a.tick, a.hours, a.log)
    host, port = hostport(a.listen, 9000)
    server = await asyncio.start_server(srv.handle, host, port)
    log.info("WaterSystem server on %s:%d, %d s per %.2f s tick (x%.0f)", host, port,
             a.step, a.tick, a.step / a.tick)
    async with server:
        clock = asyncio.create_task(srv.clock())
        await srv.done.wait()
        if a.linger > 0:  # keep answering so that clients can finish cleanly
            await asyncio.sleep(a.linger)
        clock.cancel()
    plant.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default=str(sc.DEFAULT_SCENARIO))
    ap.add_argument("--listen", default="0.0.0.0:9000")
    ap.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2025, 1, 23))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--step", type=int, default=300, help="simulated seconds per tick")
    ap.add_argument("--tick", type=float, default=1.0, help="wall-clock seconds per tick")
    ap.add_argument("--hours", type=float, default=168.0, help="simulated hours to run")
    ap.add_argument("--linger", type=float, default=5.0)
    ap.add_argument("--log", type=Path)
    a = ap.parse_args(argv)
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
