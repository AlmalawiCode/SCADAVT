"""Conformance test of a field-device program against its register map.

The test acts as the two clients of a field device: the IOModuleGate (on the
I/O-modules port) and the MTU (on the Modbus/TCP port). It checks the contract
of docs/NETWORKED_INTERFACES.md for every tag of the device:

1. before the MTU has written an output, an I/O-modules read of that output
   must answer with transaction number 0 ("not ready");
2. a sensor value written through the I/O-modules port must be readable by the
   MTU at the tag's register (input register as float32, or discrete input);
3. a command written by the MTU at the tag's register (holding register as
   float32, or coil) must be returned by an I/O-modules read with the request's
   transaction number;
4. an unknown process identifier must be answered with transaction number 0.

    python -m scadavt.deploy.conformance --name PLC1 --modbus 127.0.0.1:5020 --io 127.0.0.1:9161

The exit status is 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys

from .. import scenario as sc
from ..protocols import iomodules as io
from ..protocols import modbus as mb
from ..util import hostport


class IOClient:
    def __init__(self, host, port, timeout=5.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.counters = {io.FC_READ: 0, io.FC_WRITE: 0}

    async def connect(self):
        self.r, self.w = await asyncio.wait_for(asyncio.open_connection(self.host, self.port),
                                                self.timeout)

    async def call(self, fc, pid, value=0.0) -> io.IOMessage:
        self.counters[fc] += 1
        self.w.write(io.IOMessage(self.counters[fc], fc, pid, value).pack())
        await self.w.drain()
        reply = await asyncio.wait_for(io.read_message(self.r), self.timeout)
        if reply is None:
            raise ConnectionError("I/O modules connection closed")
        return reply

    async def close(self):
        self.w.write(io.IOMessage(0, io.FC_TERMINATE, "", 0.0).pack())
        await self.w.drain()
        self.w.close()


async def run(spec: sc.PLCSpec, modbus_addr, io_addr, seed=7) -> list[tuple[str, bool, str]]:
    rnd = random.Random(seed)
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    ioc = IOClient(*io_addr)
    await ioc.connect()
    mbc = mb.ModbusClient(*modbus_addr)
    await mbc.connect()

    outputs = [t for t in spec.tags if t.direction == "o"]
    inputs = [t for t in spec.tags if t.direction == "i"]
    for t in outputs:
        r = await ioc.call(io.FC_READ, t.name)
        check(f"{t.name}: output not ready before the MTU writes it", r.transaction == 0,
              f"transaction={r.transaction}")
    async def reconnect():
        nonlocal mbc
        await mbc.close()
        mbc = mb.ModbusClient(*modbus_addr)
        await mbc.connect()

    for t in inputs:
        v = float(rnd.choice([0, 1])) if t.is_bool else round(rnd.uniform(0.5, 5000.0), 2)
        name = f"{t.name}: MTU reads {t.reg_type}{t.address} after an I/O write"
        try:
            r = await ioc.call(io.FC_WRITE, t.name, v)
            check(f"{t.name}: I/O write acknowledged",
                  r.transaction == ioc.counters[io.FC_WRITE], f"transaction={r.transaction}")
            if t.is_bool:
                got = float((await mbc.read_bits(mb.READ_DISCRETE_INPUTS, t.address, 1))[0])
            else:
                got = mb.regs_to_float(await mbc.read_registers(mb.READ_INPUT_REGISTERS,
                                                                t.address, 2))
            check(name, math.isclose(got, v, rel_tol=1e-6), f"wrote {v}, read {got}")
        except Exception as e:  # malformed or missing reply
            check(name, False, f"{type(e).__name__}: {e}")
            await reconnect()
    for t in outputs:
        v = float(rnd.choice([0, 1])) if t.is_bool else round(rnd.uniform(0.5, 5000.0), 2)
        name = f"{t.name}: I/O read returns the MTU command"
        try:
            if t.is_bool:
                await mbc.write_coil(t.address, bool(v))
            else:
                await mbc.write_registers(t.address, mb.float_to_regs(v))
            r = await ioc.call(io.FC_READ, t.name)
            check(name, r.transaction == ioc.counters[io.FC_READ]
                  and math.isclose(r.data, v, rel_tol=1e-6),
                  f"wrote {v}, read {r.data} (transaction {r.transaction})")
        except Exception as e:
            check(name, False, f"{type(e).__name__}: {e}")
            await reconnect()
    r = await ioc.call(io.FC_READ, "NO_SUCH")
    check("unknown process ID answered with transaction 0", r.transaction == 0)
    await ioc.close()
    await mbc.close()
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default=str(sc.DEFAULT_SCENARIO))
    ap.add_argument("--name", required=True)
    ap.add_argument("--modbus", required=True)
    ap.add_argument("--io", required=True)
    a = ap.parse_args(argv)
    spec = sc.load(a.scenario).plcs[a.name]
    res = asyncio.run(run(spec, hostport(a.modbus, 502), hostport(a.io, 9161)))
    for name, ok, detail in res:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail if not ok else ''}")
    n_ok = sum(ok for _, ok, _ in res)
    print(f"{n_ok}/{len(res)} checks passed for {a.name}")
    sys.exit(0 if n_ok == len(res) else 1)


if __name__ == "__main__":
    main()
