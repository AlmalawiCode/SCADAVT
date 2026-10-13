"""MTU (master terminal unit) of the networked testbed.

Every ``--period`` wall seconds the MTU runs one polling cycle over the
emulated SCADA network:

1. it opens a Modbus/TCP connection to every PLC that holds a value needed by
   the supervisory logic, reads the values and closes the connection; the PLCs
   are polled concurrently, and a poll that has not finished by the read
   deadline is abandoned, so that one unreachable PLC does not delay the others;
2. it passes the values that arrived to ``Supervisor.decide``; a missing value
   keeps the previous decision;
3. it writes every command to its PLC (a coil for a status, two holding
   registers for a real value), concurrently and with the same deadline.

Every connection is logged to ``--connections`` and every cycle to
``--historian``. With ``--hmi`` the MTU also runs a read-only HMI server, a
Modbus/TCP slave on the back channel: input registers hold the values read
(float32, in the order of the historian columns), holding registers hold the
commands, and input register pairs are refreshed at the end of every cycle.

    python -m scadavt.master.mtu --plcs /config/mtu_plcs.ini --hmi 0.0.0.0:5020 \
        --historian /logs/historian.csv --connections /logs/connections.csv --period 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
import csv
import time
from pathlib import Path

from .. import scenario as sc
from ..protocols import modbus as mb
from ..util import hostport, setup_logging
from .control import Command, Supervisor

log = setup_logging("MTU")


class CSVLog:
    def __init__(self, path: Path | None, fields: list[str]):
        self.fh = self.w = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(path, "w", newline="")
            self.w = csv.DictWriter(self.fh, fieldnames=fields)
            self.w.writeheader()

    def write(self, row: dict) -> None:
        if self.w:
            self.w.writerow(row)

    def flush(self) -> None:
        if self.fh:
            self.fh.flush()


class MTU:
    def __init__(self, scen: sc.Scenario, plcs: dict[str, dict], period: float,
                 timeout: float, v1_control: bool, historian: Path | None,
                 connections: Path | None):
        self.scen, self.plcs, self.period = scen, plcs, period
        self.timeout = min(timeout, 0.45 * period)  # read and write deadline
        self.sup = Supervisor(scen.control, scen.doc["tank_max_level"], v1_control=v1_control)
        self.reads = self.sup.required_reads()
        self.by_plc: dict[str, list[sc.Tag]] = {}
        for plc, tag in self.reads:
            self.by_plc.setdefault(plc, []).append(scen.plcs[plc].tag(tag))
        cfg = scen.control
        self.cmd_keys = [(g["plc"], f"{p}_ST_O") for g in cfg["pump_groups"] for p in g["pumps"]]
        self.cmd_keys.append((cfg["v1"]["plc"], cfg["v1"]["setpoint_tag"]))
        self.read_cols = [f"{p}.{t}" for p, t in self.reads]
        self.cmd_cols = [f"cmd.{p}.{t}" for p, t in self.cmd_keys]
        self.hist = CSVLog(historian, ["wall_time", "cycle", "cycle_ms"] + self.read_cols
                           + self.cmd_cols)
        self.conn = CSVLog(connections, ["wall_time", "cycle", "plc", "op", "ok", "connect_ms",
                                         "request_ms", "error"])
        self.cycle = 0
        self.hmi = mb.DataBank(holding=2 * len(self.cmd_keys), inputs=2 * len(self.reads))
        self.last: dict = {}

    # ------------------------------------------------------------ one PLC
    async def _session(self, plc: str, op: str, work) -> object:
        """Open a connection to ``plc``, run ``work(client)``, close, and log."""
        a = self.plcs[plc]
        c = mb.ModbusClient(a["ip"], int(a.get("port", 502)), int(a.get("unit", 1)),
                            timeout=self.timeout)
        row = {"wall_time": round(time.time(), 3), "cycle": self.cycle, "plc": plc, "op": op,
               "ok": 0, "connect_ms": "", "request_ms": "", "error": ""}
        t0 = time.perf_counter()
        try:
            await c.connect()
            row["connect_ms"] = round(1000 * c.connect_time, 2)
            t1 = time.perf_counter()
            result = await work(c)
            row["request_ms"] = round(1000 * (time.perf_counter() - t1), 2)
            row["ok"] = 1
            return result
        except asyncio.CancelledError:
            row["error"] = f"deadline after {1000 * (time.perf_counter() - t0):.0f} ms"
            return None
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"[:120] or type(e).__name__
            return None
        finally:
            self.conn.write(row)
            await c.close()

    async def _read_plc(self, plc: str, tags: list[sc.Tag]) -> dict:
        async def work(c: mb.ModbusClient):
            out = {}
            for t in tags:
                if t.is_bool:
                    v = float((await c.read_bits(mb.READ_DISCRETE_INPUTS, t.address, 1))[0])
                else:
                    v = mb.regs_to_float(await c.read_registers(mb.READ_INPUT_REGISTERS,
                                                                t.address, 2))
                out[(plc, t.name)] = v
            return out
        return await self._session(plc, "read", work) or {}

    async def _write_plc(self, plc: str, cmds: list[Command]) -> None:
        async def work(c: mb.ModbusClient):
            for cmd in cmds:
                t = self.scen.plcs[plc].tag(cmd.tag)
                if t.is_bool:
                    await c.write_coil(t.address, cmd.value >= 0.5)
                else:
                    await c.write_registers(t.address, mb.float_to_regs(cmd.value))
            return True
        await self._session(plc, "write", work)

    @staticmethod
    async def _bounded(coros, deadline: float) -> list:
        tasks = [asyncio.ensure_future(x) for x in coros]
        if not tasks:
            return []
        done, pending = await asyncio.wait(tasks, timeout=deadline)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return [t.result() for t in tasks if t in done and not t.cancelled()
                and t.exception() is None]

    # ---------------------------------------------------------- one cycle
    async def run_cycle(self) -> None:
        self.cycle += 1
        t0 = time.perf_counter()
        values: dict = {}
        for part in await self._bounded([self._read_plc(p, tags)
                                         for p, tags in self.by_plc.items()], self.timeout):
            values.update(part or {})
        cmds = self.sup.decide(values)
        per_plc: dict[str, list[Command]] = {}
        for c in cmds:
            per_plc.setdefault(c.plc, []).append(c)
        await self._bounded([self._write_plc(p, cs) for p, cs in per_plc.items()], self.timeout)
        row = {"wall_time": round(time.time(), 3), "cycle": self.cycle,
               "cycle_ms": round(1000 * (time.perf_counter() - t0), 1)}
        for col, key in zip(self.read_cols, self.reads):
            row[col] = values.get(key, "")
        given = {(c.plc, c.tag): c.value for c in cmds}
        for col, key in zip(self.cmd_cols, self.cmd_keys):
            row[col] = given.get(key, "")
        self.hist.write(row)
        self.hist.flush()
        self.conn.flush()
        self._update_hmi(values, given)

    def _update_hmi(self, values: dict, given: dict) -> None:
        a, h = self.hmi.tables["A"], self.hmi.tables["H"]
        for i, key in enumerate(self.reads):
            if key in values:
                a[2 * i:2 * i + 2] = mb.float_to_regs(values[key])
        for i, key in enumerate(self.cmd_keys):
            if key in given:
                h[2 * i:2 * i + 2] = mb.float_to_regs(given[key])

    async def run(self, cycles: int = 0) -> None:
        next_t = time.monotonic()
        while cycles <= 0 or self.cycle < cycles:
            await self.run_cycle()
            next_t += self.period
            delay = next_t - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_t = time.monotonic()  # overran: start the next cycle now


def read_plcs(path: Path) -> dict[str, dict]:
    c = configparser.ConfigParser()
    c.optionxform = str
    c.read(path)
    return {s: dict(c[s]) for s in c.sections()}


async def main_async(a) -> None:
    scen = sc.load(a.scenario)
    mtu = MTU(scen, read_plcs(a.plcs), a.period, a.timeout, not a.no_v1_control,
              a.historian, a.connections)
    log.info("polling %d PLCs every %.2f s (deadline %.2f s), V1 control %s",
             len(mtu.by_plc), a.period, mtu.timeout, "off" if a.no_v1_control else "on")
    if a.hmi:
        host, port = hostport(a.hmi, 5020)
        await asyncio.start_server(mtu.hmi.serve, host, port)
        log.info("HMI server on %s:%d (%d values, %d commands)", host, port, len(mtu.reads),
                 len(mtu.cmd_keys))
    await mtu.run(a.cycles)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default=str(sc.DEFAULT_SCENARIO))
    ap.add_argument("--plcs", type=Path, required=True, help="INI file: [PLCn] ip, port, unit")
    ap.add_argument("--period", type=float, default=1.0, help="cycle length (wall seconds)")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--hmi", default="", help="host:port of the HMI server (empty = none)")
    ap.add_argument("--historian", type=Path)
    ap.add_argument("--connections", type=Path)
    ap.add_argument("--no-v1-control", action="store_true", help="V1 keeps its fixed setting")
    ap.add_argument("--cycles", type=int, default=0, help="stop after N cycles (0 = never)")
    asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
