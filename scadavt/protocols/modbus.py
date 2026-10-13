"""Minimal Modbus/TCP codec and asyncio client.

Only the public function codes used by a typical SCADA master are covered:
read coils (1), read discrete inputs (2), read holding registers (3), read
input registers (4), write single coil (5), write single register (6), write
multiple coils (15) and write multiple registers (16).

The codec is shared by the MTU (client) and by the attack tools, which need to
parse and rewrite Modbus traffic in flight.
"""

from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass

MBAP = struct.Struct(">HHHB")  # transaction, protocol, length, unit
MBAP_SIZE = MBAP.size  # 7

READ_COILS = 1
READ_DISCRETE_INPUTS = 2
READ_HOLDING_REGISTERS = 3
READ_INPUT_REGISTERS = 4
WRITE_SINGLE_COIL = 5
WRITE_SINGLE_REGISTER = 6
WRITE_MULTIPLE_COILS = 15
WRITE_MULTIPLE_REGISTERS = 16


def float_to_regs(value: float) -> list[int]:
    hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [hi, lo]


def regs_to_float(regs) -> float:
    return struct.unpack(">f", struct.pack(">HH", regs[0], regs[1]))[0]


def pack_bits(bits) -> bytes:
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def unpack_bits(data: bytes, count: int) -> list[bool]:
    return [bool(data[i // 8] >> (i % 8) & 1) for i in range(count)]


@dataclass
class Frame:
    transaction: int
    unit: int
    pdu: bytes

    @property
    def function(self) -> int:
        return self.pdu[0] if self.pdu else 0

    def pack(self) -> bytes:
        return MBAP.pack(self.transaction, 0, len(self.pdu) + 1, self.unit) + self.pdu


async def read_frame(reader: asyncio.StreamReader) -> Frame | None:
    try:
        head = await reader.readexactly(MBAP_SIZE)
        tid, _, length, unit = MBAP.unpack(head)
        pdu = await reader.readexactly(length - 1)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        return None
    return Frame(tid, unit, pdu)


class ModbusError(Exception):
    pass


class ModbusClient:
    """One Modbus/TCP connection with explicit, measurable connect and request steps."""

    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 5.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self.reader = self.writer = None
        self._tid = 0
        self.connect_time: float | None = None

    async def connect(self) -> float:
        t0 = time.perf_counter()
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)
        self.connect_time = time.perf_counter() - t0
        return self.connect_time

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), 1.0)
            except Exception:
                pass
            self.writer = None

    async def request(self, pdu: bytes) -> bytes:
        self._tid = (self._tid + 1) & 0xFFFF
        self.writer.write(Frame(self._tid, self.unit, pdu).pack())
        await self.writer.drain()
        frame = await asyncio.wait_for(read_frame(self.reader), self.timeout)
        if frame is None:
            raise ModbusError("connection closed")
        if frame.function & 0x80:
            raise ModbusError(f"exception code {frame.pdu[1]} for function {pdu[0]}")
        return frame.pdu

    # ------------------------------------------------------------- reads
    async def read_registers(self, fc: int, address: int, count: int) -> list[int]:
        pdu = await self.request(struct.pack(">BHH", fc, address, count))
        n = pdu[1]
        return list(struct.unpack(f">{n // 2}H", pdu[2:2 + n]))

    async def read_bits(self, fc: int, address: int, count: int) -> list[bool]:
        pdu = await self.request(struct.pack(">BHH", fc, address, count))
        return unpack_bits(pdu[2:2 + pdu[1]], count)

    # ------------------------------------------------------------ writes
    async def write_coil(self, address: int, value: bool) -> None:
        await self.request(struct.pack(">BHH", WRITE_SINGLE_COIL, address,
                                       0xFF00 if value else 0x0000))

    async def write_registers(self, address: int, values: list[int]) -> None:
        await self.request(struct.pack(f">BHHB{len(values)}H", WRITE_MULTIPLE_REGISTERS,
                                       address, len(values), 2 * len(values), *values))

    async def write_coils(self, address: int, values: list[bool]) -> None:
        data = pack_bits(values)
        await self.request(struct.pack(">BHHB", WRITE_MULTIPLE_COILS, address,
                                       len(values), len(data)) + data)


# ------------------------------------------------------------------ server
ILLEGAL_FUNCTION = 1
ILLEGAL_ADDRESS = 2
ILLEGAL_VALUE = 3
DEVICE_BUSY = 6


class DataBank:
    """The four Modbus tables of a slave, with a record of what clients wrote.

    ``written`` holds ``("C", address)`` and ``("H", address)`` for every coil
    and holding register that a client has written at least once, so that the
    owner can tell a command from the initial zero of the table.

    With ``guard_inputs`` a read of an input register or discrete input that
    the owner has never set (``mark_input``) is answered with exception 6
    (device busy), so that a master never takes the initial zero of a sensor
    for a measurement.
    """

    def __init__(self, coils: int = 0, discrete: int = 0, holding: int = 0, inputs: int = 0,
                 guard_inputs: bool = False):
        self.tables = {"C": [False] * coils, "D": [False] * discrete,
                       "H": [0] * holding, "A": [0] * inputs}
        self.written: set[tuple[str, int]] = set()
        self.guard_inputs = guard_inputs
        self.inputs_set: set[tuple[str, int]] = set()

    def mark_input(self, table: str, address: int, count: int = 1) -> None:
        self.inputs_set.update((table, address + i) for i in range(count))

    def _range(self, table: str, address: int, count: int) -> bool:
        return count >= 1 and address >= 0 and address + count <= len(self.tables[table])

    def handle(self, pdu: bytes) -> bytes:
        """Answer one request PDU with a response or exception PDU."""
        fc = pdu[0] if pdu else 0
        try:
            if fc in (READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS,
                      READ_INPUT_REGISTERS):
                address, count = struct.unpack(">HH", pdu[1:5])
                table = {READ_COILS: "C", READ_DISCRETE_INPUTS: "D",
                         READ_HOLDING_REGISTERS: "H", READ_INPUT_REGISTERS: "A"}[fc]
                if not self._range(table, address, count):
                    return bytes([fc | 0x80, ILLEGAL_ADDRESS])
                if self.guard_inputs and table in "AD" and any(
                        (table, address + i) not in self.inputs_set for i in range(count)):
                    return bytes([fc | 0x80, DEVICE_BUSY])
                values = self.tables[table][address:address + count]
                if table in "CD":
                    data = pack_bits(values)
                else:
                    data = struct.pack(f">{count}H", *values)
                return bytes([fc, len(data)]) + data
            if fc == WRITE_SINGLE_COIL:
                address, value = struct.unpack(">HH", pdu[1:5])
                if value not in (0x0000, 0xFF00):
                    return bytes([fc | 0x80, ILLEGAL_VALUE])
                if not self._range("C", address, 1):
                    return bytes([fc | 0x80, ILLEGAL_ADDRESS])
                self.tables["C"][address] = value == 0xFF00
                self.written.add(("C", address))
                return pdu[:5]
            if fc == WRITE_SINGLE_REGISTER:
                address, value = struct.unpack(">HH", pdu[1:5])
                if not self._range("H", address, 1):
                    return bytes([fc | 0x80, ILLEGAL_ADDRESS])
                self.tables["H"][address] = value
                self.written.add(("H", address))
                return pdu[:5]
            if fc == WRITE_MULTIPLE_COILS:
                address, count, n = struct.unpack(">HHB", pdu[1:6])
                if not self._range("C", address, count) or n != (count + 7) // 8:
                    return bytes([fc | 0x80, ILLEGAL_ADDRESS])
                for i, b in enumerate(unpack_bits(pdu[6:6 + n], count)):
                    self.tables["C"][address + i] = b
                    self.written.add(("C", address + i))
                return pdu[:5]
            if fc == WRITE_MULTIPLE_REGISTERS:
                address, count, n = struct.unpack(">HHB", pdu[1:6])
                if not self._range("H", address, count) or n != 2 * count:
                    return bytes([fc | 0x80, ILLEGAL_ADDRESS])
                for i, v in enumerate(struct.unpack(f">{count}H", pdu[6:6 + n])):
                    self.tables["H"][address + i] = v
                    self.written.add(("H", address + i))
                return pdu[:5]
        except struct.error:
            return bytes([fc | 0x80, ILLEGAL_VALUE])
        return bytes([(fc | 0x80) & 0xFF, ILLEGAL_FUNCTION])

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one Modbus/TCP connection until the client closes it."""
        try:
            while (frame := await read_frame(reader)) is not None:
                writer.write(Frame(frame.transaction, frame.unit, self.handle(frame.pdu)).pack())
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
