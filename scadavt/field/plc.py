"""Field device simulator: Modbus/TCP slave + I/O Modules simulator.

One process represents one PLC. It runs two servers:

* a Modbus/TCP slave (pymodbus) on the emulated SCADA network. The MTU reads
  the PLC's input registers and discrete inputs and writes its holding
  registers and coils;
* the I/O Modules simulator on the back channel. The IOModuleGate writes the
  measured plant values into the PLC's inputs and reads the actuator values
  from the PLC's outputs, using the IOModules protocol.

The register map follows the tag list of the PLC in the scenario file.

    python -m scadavt.field.plc --name PLC1 --modbus 0.0.0.0:502 --io 0.0.0.0:9161
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from pymodbus.datastore import (ModbusSequentialDataBlock, ModbusServerContext,
                                ModbusSlaveContext)
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.server import ModbusTcpServer

from .. import scenario as sc
from ..protocols import iomodules as io
from ..protocols.modbus import float_to_regs, regs_to_float
from ..util import hostport, setup_logging

log = logging.getLogger("plc")

FC_OF = {"A": 4, "H": 3, "D": 2, "C": 1}  # register type -> read function code


class OutputBlock(ModbusSequentialDataBlock):
    """Data block that remembers which addresses the master has written."""

    def __init__(self, address, values):
        super().__init__(address, values)
        self.written: set[int] = set()

    def setValues(self, address, values):  # noqa: N802 (pymodbus API)
        super().setValues(address, values)
        n = len(values) if isinstance(values, list) else 1
        self.written.update(range(address, address + n))


class PLC:
    def __init__(self, spec: sc.PLCSpec):
        self.spec = spec
        size = 2 * len(spec.tags) + 4
        self.blocks = {
            "D": ModbusSequentialDataBlock(0, [0] * size),
            "C": OutputBlock(0, [0] * size),
            "A": ModbusSequentialDataBlock(0, [0] * size),
            "H": OutputBlock(0, [0] * size),
        }
        self.store = ModbusSlaveContext(di=self.blocks["D"], co=self.blocks["C"],
                                        ir=self.blocks["A"], hr=self.blocks["H"])
        self.context = ModbusServerContext(slaves=self.store, single=True)
        self.tags = {t.name: t for t in spec.tags}

    # values are accessed through the slave context so that the address offset
    # applied by pymodbus is the same for the master and for the I/O modules
    def set_input(self, tag: sc.Tag, value: float) -> None:
        if tag.is_bool:
            self.store.setValues(FC_OF["D"], tag.address, [1 if value >= 0.5 else 0])
        else:
            self.store.setValues(FC_OF["A"], tag.address, float_to_regs(value))

    def get_output(self, tag: sc.Tag) -> float | None:
        block = self.blocks[tag.reg_type]
        if tag.address + 1 not in block.written:  # pymodbus stores at address+1
            return None  # the master has not written this output yet
        if tag.is_bool:
            return float(self.store.getValues(FC_OF["C"], tag.address, 1)[0])
        return regs_to_float(self.store.getValues(FC_OF["H"], tag.address, 2))

    # ------------------------------------------------------- I/O modules
    async def handle_io(self, reader, writer):
        peer = writer.get_extra_info("peername")
        log.info("I/O modules session from %s", peer)
        try:
            while True:
                msg = await io.read_message(reader)
                if msg is None or msg.function == io.FC_TERMINATE:
                    break
                tag = self.tags.get(msg.pid)
                if tag is None:
                    reply = io.IOMessage(0, msg.function, msg.pid, 0.0)
                elif msg.function == io.FC_WRITE and tag.direction == "i":
                    self.set_input(tag, msg.data)
                    reply = io.IOMessage(msg.transaction, msg.function, msg.pid, msg.data)
                elif msg.function == io.FC_READ and tag.direction == "o":
                    v = self.get_output(tag)
                    reply = (io.IOMessage(0, msg.function, msg.pid, 0.0) if v is None
                             else io.IOMessage(msg.transaction, msg.function, msg.pid, v))
                else:
                    reply = io.IOMessage(0, msg.function, msg.pid, 0.0)
                writer.write(reply.pack())
                await writer.drain()
        finally:
            writer.close()


async def serve(spec: sc.PLCSpec, modbus_addr, io_addr):
    plc = PLC(spec)
    ident = ModbusDeviceIdentification()
    ident.VendorName = "SCADAVT"
    ident.ProductCode = "SCADAVT-PLC"
    ident.ProductName = f"SCADAVT field device {spec.name}"
    ident.ModelName = "virtual PLC"
    ident.MajorMin