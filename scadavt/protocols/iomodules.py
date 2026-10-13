"""IOModules protocol.

A small TCP protocol that carries measurement and control data between the
IOModuleGate (outside the emulated network) and the I/O Modules simulator that
runs on every emulated field device. One protocol data unit (PDU) is 19 bytes:

    +---------------+---------------+-------------+--------+
    | TransactionNo | Function code |  ProcessID  |  Data  |
    |    4 bytes    |    1 byte     |  10 bytes   | 4 bytes|
    +---------------+---------------+-------------+--------+

TransactionNo  unsigned 32-bit counter. Reads and writes are numbered
               independently and both start at 1. A response echoes the
               request number. A response number of 0 means "output not ready,
               ask again later".
Function code  1 = read, 2 = write, 0 = terminate the session.
ProcessID      ASCII identifier of a process parameter, right-padded with NUL.
Data           IEEE-754 float32 value (0 in a read request).

All fields are big-endian (network byte order).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PDU = struct.Struct(">IB10sf")
PDU_SIZE = PDU.size  # 19

FC_TERMINATE = 0
FC_READ = 1
FC_WRITE = 2

PID_LEN = 10


def encode_pid(pid: str) -> bytes:
    raw = pid.encode("ascii")
    if len(raw) > PID_LEN:
        raise ValueError(f"ProcessID '{pid}' is longer than {PID_LEN} bytes")
    return raw.ljust(PID_LEN, b"\x00")


def decode_pid(raw: bytes) -> str:
    return raw.rstrip(b"\x00").decode("ascii")


@dataclass
class IOMessage:
    transaction: int
    function: int
    pid: str
    data: float = 0.0

    def pack(self) -> bytes:
        return PDU.pack(self.transaction & 0xFFFFFFFF, self.function,
                        encode_pid(self.pid), float(self.data))

    @classmethod
    def unpack(cls, raw: bytes) -> "IOMessage":
        t, f, p, d = PDU.unpack(raw)
        return cls(t, f, decode_pid(p), d)


async def read_message(reader) -> IOMessage | None:
    """Read one PDU from an asyncio StreamReader; None on clean EOF."""
    try:
        raw = await reader.readexactly(PDU_SIZE)
    except Exception:
        return None
    return IOMessage.unpack(raw)
