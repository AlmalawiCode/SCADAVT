"""WaterSystem server protocol.

A small TCP protocol through which a client acquires or changes the simulated
state of the water distribution system (WDS). One PDU is 17 bytes:

    +-----------+---------------+----------------+-----------+--------+
    | ProcessID | Function code | Parameter type | Data type |  Data  |
    | 10 bytes  |    1 byte     |     1 byte     |  1 byte   | 4 bytes|
    +-----------+---------------+----------------+-----------+--------+

ProcessID       ASCII identifier of the process parameter (NUL padded).
                The server maps it to an EPANET node or link.
Function code   1 = acquisition, 2 = control. The response echoes the code,
                or returns 0xFF when the request failed.
Parameter type  0 = node (junction, tank), 1 = link (pipe, pump, valve).
Data type       the quantity being read or written (see DT_* below).
Data            IEEE-754 float32 value (0 in an acquisition request).

The special ProcessID "SIMCLOCK" (acquisition only) returns the simulated time
in hours, so that clients can time-stamp what they observe.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .iomodules import decode_pid, encode_pid

PDU = struct.Struct(">10sBBBf")
PDU_SIZE = PDU.size  # 17

FC_ACQUIRE = 1
FC_CONTROL = 2
FC_ERROR = 0xFF

PT_NODE = 0
PT_LINK = 1

# Data types (kept independent of EPANET's own enumeration so that the wire
# format does not change if the solver changes).
DT_LEVEL = 1      # tank water level (m)
DT_PRESSURE = 2   # node pressure (m)
DT_DEMAND = 3     # delivered demand at a node (L/min)
DT_HEAD = 4       # hydraulic head (m)
DT_VOLUME = 5     # tank volume (m3)
DT_FLOW = 10      # link flow (L/min)
DT_STATUS = 11    # link status: 1 open/on, 0 closed/off
DT_SETTING = 12   # valve setting (L/min for FCV, m for PRV) or pump speed
DT_SPEED = 13     # pump relative speed
DT_ENERGY = 14    # pump power (kW)
DT_DEFICIT = 15   # unmet demand at a node (L/min)

DATA_TYPE_NAMES = {
    DT_LEVEL: "level", DT_PRESSURE: "pressure", DT_DEMAND: "demand",
    DT_HEAD: "head", DT_VOLUME: "volume", DT_FLOW: "flow",
    DT_STATUS: "status", DT_SETTING: "setting", DT_SPEED: "speed",
    DT_ENERGY: "energy", DT_DEFICIT: "deficit",
}
DATA_TYPE_CODES = {v: k for k, v in DATA_TYPE_NAMES.items()}


@dataclass
class WDSMessage:
    pid: str
    function: int
    ptype: int
    dtype: int
    data: float = 0.0

    def pack(self) -> bytes:
        return PDU.pack(encode_pid(self.pid), self.function, self.ptype,
                        self.dtype, float(self.data))

    @classmethod
    def unpack(cls, raw: bytes) -> "WDSMessage":
        p, f, pt, dt, d = PDU.unpack(raw)
        return cls(decode_pid(p), f, pt, dt, d)
