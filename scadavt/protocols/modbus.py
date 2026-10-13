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
