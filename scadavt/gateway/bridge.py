"""IOModuleGate and meta-converter: the bridge between the plant and the PLCs.

``IOModuleGate`` keeps one IOModules connection (back channel) to every field
device, as configured by the two INI files of the paper: file 1 names the
address of each device, file 2 names the device and direction of every
process parameter. ``write`` sends sensor values to the devices; ``read``
collects the commands that the MTU has written into them (a reply with
transaction 0 means "not ready" and is skipped).

The meta-converter runs every ``--period`` wall seconds: it acquires every
sensor value from the WaterSystem server, writes them to the devices, reads
the commands, and sends every command that changed since the previous cycle to
the server.

    python -m scadavt.gateway.bridge --plcs /config/iomodulegate_plcs.ini \
        --tags /config/iomodulegate_tags.ini --wds 172.16.0.20:9000 --period 1.0 \
        --log /logs/gateway.csv
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
import csv
import time
from dataclasses import dataclass
from pathlib import Path

from ..protocols import iomodules as io
from ..protocols import wdsproto as wp
from ..util import hostport, setup_logging
from ..wds.client import WDSClient

log = setup_logging("GATEWAY")


@dataclass
class Param:
    pid: str
    controller: str
    direction: str
    element: str
    ptype: int
    dtype: int


def _ini(path: Path) -> configparser.ConfigParser:
    c = configparser.ConfigParser()
    c.optionxform = str
    c.read(path)
    return c


def load_params(path: Path) -> list[Param]:
    c = _ini(path)
    return [Param(s, c[s]["controller"], c[s]["paraType"], c[s]["element"],
                  wp.PT_NODE if c[s]["elementType"] == "node" else wp.PT_LINK,
                  wp.DATA_TYPE_CODES[c[s]["dataType"]]) for s in c.sections()]


class DeviceLink:
    """One IOModules session with a field device; reconnects after an error."""

    def __init__(self, name: str, host: str, port: int, timeout: float):
        self.name, self.host, self.port, self.timeout = name, host, port, timeout
        self.r = self.w = None
        self.counters = {io.FC_READ: 0, io.FC_WRITE: 0}

    async def _call(self, fc: int, pid: str, value: float = 0.0) -> io.IOMessage:
        if self.w is None:
            self.r, self.w = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout)
        self.counters[fc] = self.counters[fc] % 0xFFFFFFFF + 1
        self.w.write(io.IOMessage(self.counters[fc], fc, pid, value).pack())
        await self.w.drain()
        reply = await asyncio.wait_for(io.read_message(self.r), self.timeout)
        if reply is None:
            raise ConnectionError(f"{self.name} closed the I/O session")
        return reply

    def drop(self) -> None:
        if self.w is not None:
            self.w.close()
        self.r = self.w = None

    async def write(self, items: list[tuple[str, float]]) -> int:
        n = 0
        for pid, value in items:
            r = await self._call(io.FC_WRITE, pid, value)
            n += r.transaction == self.counters[io.FC_WRITE]
        return n

    async def read(self, pids: list[str]) -> dict[str, float]:
        out = {}
        for pid in pids:
            r = await self._call(io.FC_READ, pid)
            if r.transaction != 0:
                out[pid] = r.data
        return out

    async def close(self) -> None:
        if self.w is not None:
            self.w.write(io.IOMessage(0, io.FC_TERMINATE, "", 0.0).pack())
            await self.w.drain()
            self.drop()


class IOModuleGate:
    def __init__(self, plcs_ini: Path, tags_ini: Path, timeout: float = 2.0):
        c = _ini(plcs_ini)
        self.devices = {s: DeviceLink(s, c[s]["ip"], int(c[s]["port"]), timeout)
                        for s in c.sections()}
        self.params = {p.pid: p for p in load_params(tags_ini)}
        self.outputs: dict[str, list[str]] = {}
        for p in self.params.values():
            if p.direction == "o":
                self.outputs.setdefault(p.controller, []).append(p.pid)
        self.errors = 0

    async def _each(self, jobs: dict[str, object]) -> list:
        async def guard(name, coro):
            try:
                return await coro
            except Exception as e:
                self.errors += 1
                self.devices[name].drop()
                log.debug("%s: %s", name, e)
                return None
        return await asyncio.gather(*(guard(n, c) for n, c in jobs.items()))

    async def write(self, values: dict[str, float]) -> int:
        per: dict[str, list] = {}
        for pid, v in values.items():
            per.setdefault(self.params[pid].controller, []).append((pid, v))
        res = await self._each({n: self.devices[n].write(items) for n, items in per.items()})
        return sum(r or 0 for r in res)

    async def read(self) -> dict[str, float]:
        res = await self._each({n: self.devices[n].read(pids) for n, pids in self.outputs.items()})
        out = {}
        for r in res:
            out.update(r or {})
        return out

    async def close(self) -> None:
        for d in self.devices.values():
            try:
                await d.close()
            except Exception:
                d.drop()


class MetaConverter:
    def __init__(self, gate: IOModuleGate, wds: WDSClient, period: float, log_path: Path | None):
        self.gate, self.wds, self.period = gate, wds, period
        self.inputs = [p for p in gate.params.values() if p.direction == "i"]
        self.last: dict[str, float] = {}
        self.cycle = 0
        self.fh = self.w = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(log_path, "w", newline="")
            self.w = csv.writer(self.fh)
            self.w.writerow(["wall_time", "sim_h", "cycle", "n_inputs", "n_outputs",
                             "n_controls", "cycle_ms", "errors"])

    async def _ensure_wds(self) -> None:
        if self.wds.writer is None:
            await self.wds.connect()

    async def run_cycle(self) -> None:
        self.cycle += 1
        t0 = time.perf_counter()
        errors0 = self.gate.errors
        await self._ensure_wds()
        sim_h = await self.wds.sim_hours()
        values = {p.pid: await self.wds.acquire(p.element, p.ptype, p.dtype) for p in self.inputs}
        n_in = await self.gate.write(values)
        outputs = await self.gate.read()
        n_ctl = 0
        for pid, v in outputs.items():
            if self.last.get(pid) != v:
                p = self.gate.params[pid]
                await self.wds.control(p.element, p.ptype, p.dtype, v)
                self.last[pid] = v
                n_ctl += 1
        if self.w:
            self.w.writerow([round(time.time(), 3), round(sim_h, 4), self.cycle, n_in,
                             len(outputs), n_ctl, round(1000 * (time.perf_counter() - t0), 1),
                             self.gate.errors - errors0])
            self.fh.flush()

    async def run(self) -> None:
        next_t = time.monotonic()
        while True:
            try:
                await self.run_cycle()
            except Exception as e:  # plant not up yet, or finished
                log.warning("cycle %d: %s: %s", self.cycle, type(e).__name__, e)
                await self.wds.close()
            next_t += self.period
            delay = next_t - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_t = time.monotonic()


async def main_async(a) -> None:
    gate = IOModuleGate(a.plcs, a.tags, a.timeout)
    host, port = hostport(a.wds, 9000)
    mc = MetaConverter(gate, WDSClient(host, port), a.period, a.log)
    log.info("%d devices, %d inputs, %d outputs, plant at %s:%d, period %.2f s",
             len(gate.devices), len(mc.inputs), sum(map(len, gate.outputs.values())),
             host, port, a.period)
    try:
        await mc.run()
    finally:
        await gate.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plcs", type=Path, required=True, help="IOModuleGate file 1")
    ap.add_argument("--tags", type=Path, required=True, help="IOModuleGate file 2")
    ap.add_argument("--wds", default="172.16.0.20:9000")
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--log", type=Path)
    asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
