"""Field device (PLC) of the networked testbed.

The device has two faces, as in the original SCADAVT:

* on the emulated SCADA network it is a Modbus/TCP slave (unit 1) whose
  register map comes from the scenario (sensor values in input registers and
  discrete inputs, commands in holding registers and coils);
* on the back channel it runs the I/O Modules simulator, which speaks the
  IOModules protocol with the IOModuleGate: the gateway writes sensor values
  into the device and reads the commands that the MTU has written.

The device is passive: it keeps no control logic of its own. Until the
gateway has delivered a sensor value, a Modbus read of that value is answered
with exception 6 (device busy) instead of the initial zero.

    python -m scadavt.field.plc --name PLC6 --modbus 0.0.0.0:502 --io 0.0.0.0:9161
"""

from __future__ import annotations

import argparse
import asyncio
import struct

from .. import scenario as sc
from ..protocols import iomodules as io
from ..protocols import modbus as mb
from ..util import hostport, setup_logging


class FieldDevice:
    def __init__(self, spec: sc.PLCSpec):
        self.spec = spec
        self.tags = {t.name: t for t in spec.tags}
        size = {"A": 0, "H": 0, "D": 0, "C": 0}
        for t in spec.tags:
            size[t.reg_type] = max(size[t.reg_type], t.address + (1 if t.is_bool else 2))
        self.bank = mb.DataBank(coils=size["C"], discrete=size["D"], holding=size["H"],
                                inputs=size["A"], guard_inputs=True)
        self.io_requests = 0

    # ----------------------------------------------------- process image
    def set_input(self, t: sc.Tag, value: float) -> None:
        tables = self.bank.tables
        if t.is_bool:
            tables["D"][t.address] = value >= 0.5
            self.bank.mark_input("D", t.address)
        else:
            tables["A"][t.address:t.address + 2] = mb.float_to_regs(value)
            self.bank.mark_input("A", t.address, 2)

    def get_output(self, t: sc.Tag) -> float | None:
        """The last command of the MTU, or None if it has not written it yet."""
        tables, written = self.bank.tables, self.bank.written
        if t.is_bool:
            if ("C", t.address) not in written:
                return None
            return 1.0 if tables["C"][t.address] else 0.0
        if ("H", t.address) not in written or ("H", t.address + 1) not in written:
            return None
        return mb.regs_to_float(tables["H"][t.address:t.address + 2])

    # ---------------------------------------------------- I/O modules side
    def answer(self, m: io.IOMessage) -> io.IOMessage:
        self.io_requests += 1
        t = self.tags.get(m.pid)
        if m.function == io.FC_WRITE and t is not None and t.direction == "i":
            self.set_input(t, m.data)
            return io.IOMessage(m.transaction, m.function, m.pid, m.data)
        if m.function == io.FC_READ and t is not None and t.direction == "o":
            value = self.get_output(t)
            if value is not None:
                return io.IOMessage(m.transaction, m.function, m.pid, value)
        return io.IOMessage(0, m.function, m.pid, 0.0)  # not ready / not known

    async def serve_io(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while (m := await io.read_message(reader)) is not None:
                if m.function == io.FC_TERMINATE:
                    break
                writer.write(self.answer(m).pack())
                await writer.drain()
        except (ConnectionError, OSError, struct.error, UnicodeDecodeError):
            pass
        finally:
            writer.close()


async def run(a) -> None:
    log = setup_logging(a.name)
    spec = sc.load(a.scenario).plcs[a.name]
    dev = FieldDevice(spec)
    mhost, mport = hostport(a.modbus, 502)
    ihost, iport = hostport(a.io, 9161)
    s1 = await asyncio.start_server(dev.bank.serve, mhost, mport)
    s2 = await asyncio.start_server(dev.serve_io, ihost, iport)
    log.info("%s: Modbus/TCP on %s:%d, I/O modules on %s:%d, %d tags", a.name, mhost, mport,
             ihost, iport, len(spec.tags))
    async with s1, s2:
        await asyncio.gather(s1.serve_forever(), s2.serve_forever())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default=str(sc.DEFAULT_SCENARIO))
    ap.add_argument("--name", required=True)
    ap.add_argument("--modbus", default="0.0.0.0:502")
    ap.add_argument("--io", default="0.0.0.0:9161")
    asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
