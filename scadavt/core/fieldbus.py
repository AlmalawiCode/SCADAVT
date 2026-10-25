"""Pluggable field-bus layer (register-based device model).

A field device is a **domain-neutral Modbus register image** — named
:class:`Register` entries in the four standard data areas (coils, discrete
inputs, holding registers, input registers) — plus a **pluggable field-bus
server** that exposes that image on the emulated SCADA network. Modbus/TCP is
the default, validated plugin; other protocols (DNP3, IEC-104, EtherNet/IP) can
be added behind the same two interfaces without touching the rest of the core.

Nothing here is domain-specific: a register is just ``(area, address, data
type)`` with optional scaling/word-order. Sensor/actuator meaning is **not** part
of a PLC — it belongs to the optional external adapter/mapping layer. A legacy
``(role, is_bool)`` point is accepted transitionally via :func:`DevicePoint`,
which lowers it to a register.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..protocols import modbus as mb
from . import integration as ig

# ---- Modbus areas, register widths, encodings ------------------------------
#: config ``area`` name <-> internal table letter (A input-reg, D discrete-in,
#: H holding-reg, C coil) and the conventional 5-digit reference base.
_AREA_T = {"coil": "C", "discrete_input": "D",
           "holding_register": "H", "input_register": "A"}
_T_AREA = {v: k for k, v in _AREA_T.items()}
_TABLE_NAME = dict(_T_AREA)
_REF_BASE = {"C": 1, "D": 10001, "A": 30001, "H": 40001}
AREAS = tuple(_AREA_T)
#: areas a master may WRITE over Modbus (the others are protocol read-only — a
#: master can read them but only the device's internal producer sets them).
_WRITABLE_AREAS = ("coil", "holding_register")
#: register width, in 16-bit words, per data type (bool lives in a 1-bit table).
_DTYPE_WORDS = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2}
DATA_TYPES = ("bool",) + tuple(_DTYPE_WORDS)


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _raw_to_words(raw: float, dtype: str) -> list[int]:
    """Engineering-scaled raw number -> Modbus registers (big-endian words)."""
    if dtype == "float32":
        return mb.float_to_regs(float(raw))
    iv = int(round(raw))
    if dtype == "uint16":
        return [_clamp(iv, 0, 0xFFFF)]
    if dtype == "int16":
        return list(struct.unpack(">H", struct.pack(">h", _clamp(iv, -32768, 32767))))
    if dtype == "uint32":
        return list(struct.unpack(">HH", struct.pack(">I", _clamp(iv, 0, 0xFFFFFFFF))))
    if dtype == "int32":
        return list(struct.unpack(">HH", struct.pack(">i", _clamp(iv, -(2**31), 2**31 - 1))))
    raise ValueError(f"unknown data_type {dtype!r}")


def _words_to_raw(words, dtype: str) -> float:
    if dtype == "float32":
        return mb.regs_to_float(words)
    if dtype == "uint16":
        return words[0] & 0xFFFF
    if dtype == "int16":
        return struct.unpack(">h", struct.pack(">H", words[0] & 0xFFFF))[0]
    if dtype == "uint32":
        return struct.unpack(">I", struct.pack(">HH", words[0] & 0xFFFF, words[1] & 0xFFFF))[0]
    if dtype == "int32":
        return struct.unpack(">i", struct.pack(">HH", words[0] & 0xFFFF, words[1] & 0xFFFF))[0]
    raise ValueError(f"unknown data_type {dtype!r}")


# legacy (role, is_bool) -> area  — the transitional alias lowering
def _legacy_area(role: str, is_bool: bool) -> str:
    is_input = role in (ig.MEASUREMENT, ig.STATE)
    if is_bool:
        return "discrete_input" if is_input else "coil"
    return "input_register" if is_input else "holding_register"


@dataclass
class Register:
    """One Modbus data point of a PLC: an area + address + data type. Carries no
    sensor/actuator meaning."""
    id: str
    area: str                       # coil | discrete_input | holding_register | input_register
    address: int = 0
    data_type: str = "float32"      # bool | uint16 | int16 | uint32 | int32 | float32
    scale: float = 1.0
    offset: float = 0.0
    word_order: str = "big"         # 'big' = high word first (Modbus default)
    initial: float = 0.0
    owner: str = ""                 # authoritative writer class (mtu|plc|process|'' = any)
    pinned: bool = False            # True: address fixed by config, not auto-assigned

    @property
    def reg_type(self) -> str:
        return _AREA_T[self.area]

    @property
    def is_bool(self) -> bool:
        return self.data_type == "bool"

    @property
    def words(self) -> int:
        """Register count (0 for a bit)."""
        return 0 if self.is_bool else _DTYPE_WORDS[self.data_type]

    @property
    def width(self) -> int:
        """Table cells occupied (1 for a bit, else the word count)."""
        return 1 if self.is_bool else self.words

    @property
    def writable(self) -> bool:
        """True if a master may write this register over Modbus."""
        return self.area in _WRITABLE_AREAS

    @property
    def role(self) -> str:
        """Legacy-compat view: a writable area reads like a command, a read-only
        area like a measurement. Nothing in the generic model depends on this."""
        return ig.COMMAND if self.writable else ig.MEASUREMENT

    # -- engineering value <-> raw registers/bit (scaling + word order) ----
    def decode(self, raw) -> float:
        if self.is_bool:
            bit = raw[0] if isinstance(raw, (list, tuple)) else raw
            return 1.0 if bit else 0.0
        words = list(raw)
        if self.word_order == "little":
            words = words[::-1]
        return _words_to_raw(words, self.data_type) * self.scale + self.offset

    def encode(self, value) -> list[int] | bool:
        if self.is_bool:
            return float(value) >= 0.5
        raw = (float(value) - self.offset) / (self.scale or 1.0)
        words = _raw_to_words(raw, self.data_type)
        if self.word_order == "little":
            words = words[::-1]
        return words


def DevicePoint(id: str, role: str, is_bool: bool = False, address: int = 0,
                pinned: bool = False) -> Register:
    """Deprecated: build a :class:`Register` from a legacy ``(role, is_bool)``
    point. The transitional alias — new configs declare registers directly."""
    return Register(id=id, area=_legacy_area(role, is_bool),
                    data_type="bool" if is_bool else "float32",
                    address=address, pinned=pinned)


def registers_from_config(node: dict) -> list[Register]:
    """Build a device's registers from a session node. Accepts the native
    ``registers`` list or (transitionally) a legacy ``points`` list, but **never
    both** — one model only."""
    has_regs, has_pts = bool(node.get("registers")), bool(node.get("points"))
    if has_regs and has_pts:
        raise ValueError(f"device {node.get('id')!r} declares both 'registers' and "
                         f"legacy 'points' — use one model")
    if has_regs:
        out = []
        for r in node["registers"]:
            dt = r.get("data_type", "float32")
            init = r.get("initial", 0.0)
            out.append(Register(
                id=r["id"], area=r["area"], address=int(r.get("address", 0)),
                data_type=dt, scale=float(r.get("scale", 1.0)),
                offset=float(r.get("offset", 0.0)), word_order=r.get("word_order", "big"),
                initial=(1.0 if init else 0.0) if dt == "bool" else float(init),
                owner=r.get("owner", ""), pinned="address" in r))
        return out
    return [DevicePoint(p["id"], p["role"], bool(p.get("is_bool", False)),
                        address=int(p.get("address", 0)), pinned="address" in p)
            for p in node.get("points", [])]


class Device:
    """A neutral Modbus register image for one field device, plus address
    assignment. Register areas obey standard Modbus read-only semantics: a master
    may write only coils/holding registers; input registers and discrete inputs
    are set only by the device's internal producer (seed, local logic, or an
    external adapter)."""

    def __init__(self, name: str, registers: list[Register]):
        self.name = name
        self.points = registers          # kept as `points` for existing callers
        self.registers = registers
        self._assign()
        size = {"A": 0, "H": 0, "D": 0, "C": 0}
        for p in registers:
            size[p.reg_type] = max(size[p.reg_type], p.address + p.width)
        self.bank = mb.DataBank(coils=size["C"], discrete=size["D"], holding=size["H"],
                                inputs=size["A"], guard_inputs=True)
        self.by_id = {p.id: p for p in registers}
        for p in registers:              # apply power-on initial values
            if p.initial:
                self._store(p, p.initial, producer=not p.writable)

    def _assign(self) -> None:
        """Place each register in its table: honour pinned addresses, pack the
        rest after any pinned ranges."""
        c = {"A": 0, "H": 0, "D": 0, "C": 0}
        for p in self.points:
            if p.pinned:
                c[p.reg_type] = max(c[p.reg_type], p.address + p.width)
        for p in self.points:
            if p.pinned:
                continue
            p.address = c[p.reg_type]
            c[p.reg_type] += p.width

    def register_map(self) -> list[dict]:
        rows = []
        for p in self.points:
            rows.append({
                "point": p.id, "area": p.area, "table": _TABLE_NAME[p.reg_type],
                "access": "read/write" if p.writable else "read-only",
                "address": p.address, "count": p.width,
                "ref": _REF_BASE[p.reg_type] + p.address,
                "data_type": p.data_type, "scale": p.scale, "offset": p.offset,
            })
        return rows

    # -- internal store: write a value into a register's table cells ---------
    def _store(self, p: Register, value: float, producer: bool) -> None:
        t = self.bank.tables
        if p.is_bool:
            t[p.reg_type][p.address] = float(value) >= 0.5
            if producer:
                self.bank.mark_input(p.reg_type, p.address)
            else:
                self.bank.written.add((p.reg_type, p.address))
            return
        words = p.encode(value)
        t[p.reg_type][p.address:p.address + p.words] = words
        if producer:
            self.bank.mark_input(p.reg_type, p.address, p.words)
        else:
            for i in range(p.words):
                self.bank.written.add((p.reg_type, p.address + i))

    # -- adapter/producer side: set a read-only register's value -------------
    def set_input(self, point_id: str, value: float) -> None:
        self._store(self.by_id[point_id], value, producer=True)

    # -- local control: write a command register directly --------------------
    def set_output(self, point_id: str, value: float) -> None:
        self._store(self.by_id[point_id], value, producer=False)

    def value(self, point_id: str) -> float:
        """Current decoded value of any register (regardless of area)."""
        p = self.by_id[point_id]
        t = self.bank.tables
        if p.is_bool:
            return 1.0 if t[p.reg_type][p.address] else 0.0
        return p.decode(t[p.reg_type][p.address:p.address + p.words])

    def get_output(self, point_id: str) -> float | None:
        """Last value written to a writable register (by a master or locally), or
        None if never written."""
        p = self.by_id[point_id]
        t, w = self.bank.tables, self.bank.written
        if p.is_bool:
            return (1.0 if t[p.reg_type][p.address] else 0.0) if (p.reg_type, p.address) in w else None
        if all((p.reg_type, p.address + i) in w for i in range(p.words)):
            return p.decode(t[p.reg_type][p.address:p.address + p.words])
        return None


# ------------------------------------------------------------------ plugins
class FieldbusServer:
    """Interface: expose a device's image on the emulated network."""
    async def serve(self, host: str, port: int): raise NotImplementedError


class FieldbusClient:
    """Interface: a master/peer reading and writing device registers over the net."""
    async def read(self, point: Register) -> float: raise NotImplementedError
    async def write(self, point: Register, value: float) -> None: raise NotImplementedError
    async def close(self) -> None: ...


class ModbusServer(FieldbusServer):
    """Default plugin: serve the device's DataBank over Modbus/TCP (reuses the
    validated ``protocols.modbus.DataBank``)."""

    def __init__(self, device: Device):
        self.device = device

    async def serve(self, host: str = "127.0.0.1", port: int = 0):
        import asyncio
        return await asyncio.start_server(self.device.bank.serve, host, port)


class ModbusClientPlugin(FieldbusClient):
    """Default plugin: read/write device registers via the validated ModbusClient."""

    def __init__(self, host: str, port: int, unit: int = 1, timeout: float = 2.0):
        self.c = mb.ModbusClient(host, port, unit, timeout=timeout)
        self._open = False

    async def _ensure(self) -> None:
        if not self._open:
            await self.c.connect()
            self._open = True

    async def read(self, point: Register) -> float:
        await self._ensure()
        if point.is_bool:
            fc = mb.READ_DISCRETE_INPUTS if point.reg_type == "D" else mb.READ_COILS
            return point.decode(await self.c.read_bits(fc, point.address, 1))
        fc = mb.READ_INPUT_REGISTERS if point.reg_type == "A" else mb.READ_HOLDING_REGISTERS
        return point.decode(await self.c.read_registers(fc, point.address, point.words))

    async def write(self, point: Register, value: float) -> None:
        await self._ensure()
        if point.is_bool:
            await self.c.write_coil(point.address, float(value) >= 0.5)
        else:
            await self.c.write_registers(point.address, point.encode(value))

    async def close(self) -> None:
        if self._open:
            await self.c.close()
            self._open = False


# ------------------------------------------------------------------ second plugin
# A deliberately simple, non-Modbus field-bus to prove the layer is pluggable and
# that devices of different protocols can coexist in one topology. It is a
# reference second plugin, not a standard industrial protocol (real DNP3 / IEC-104
# would drop in behind the same two interfaces).
import asyncio as _asyncio
import json as _json
import struct as _struct

_SLEN = _struct.Struct(">I")


class SimpleTcpServer(FieldbusServer):
    """Serve a device's DataBank over a length-prefixed JSON request protocol."""

    def __init__(self, device: Device):
        self.device = device

    def _handle(self, req: dict) -> dict:
        p = self.device.by_id.get(req.get("id"))
        if p is None:
            return {"ok": False}
        t = self.device.bank.tables
        if req["op"] == "read":
            if p.is_bool:
                return {"ok": True, "value": p.decode([t[p.reg_type][p.address]])}
            return {"ok": True, "value": p.decode(t[p.reg_type][p.address:p.address + p.words])}
        if req["op"] == "write":
            if p.is_bool:
                t[p.reg_type][p.address] = req["value"] >= 0.5
                self.device.bank.written.add((p.reg_type, p.address))
            else:
                t[p.reg_type][p.address:p.address + p.words] = p.encode(req["value"])
                for i in range(p.words):
                    self.device.bank.written.add((p.reg_type, p.address + i))
            return {"ok": True}
        return {"ok": False}

    async def _serve(self, reader, writer):
        try:
            while (head := await reader.readexactly(_SLEN.size)):
                (n,) = _SLEN.unpack(head)
                req = _json.loads(await reader.readexactly(n))
                body = _json.dumps(self._handle(req)).encode()
                writer.write(_SLEN.pack(len(body)) + body)
                await writer.drain()
        except (_asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def serve(self, host: str = "127.0.0.1", port: int = 0):
        return await _asyncio.start_server(self._serve, host, port)


class SimpleTcpClient(FieldbusClient):
    def __init__(self, host: str, port: int, unit: int = 1, timeout: float = 2.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = self.writer = None

    async def _call(self, req: dict) -> dict:
        if self.writer is None:
            self.reader, self.writer = await _asyncio.wait_for(
                _asyncio.open_connection(self.host, self.port), self.timeout)
        body = _json.dumps(req).encode()
        self.writer.write(_SLEN.pack(len(body)) + body)
        await self.writer.drain()
        head = await _asyncio.wait_for(self.reader.readexactly(_SLEN.size), self.timeout)
        (n,) = _SLEN.unpack(head)
        return _json.loads(await self.reader.readexactly(n))

    async def read(self, point: Register) -> float:
        return float((await self._call({"op": "read", "id": point.id}))["value"])

    async def write(self, point: Register, value: float) -> None:
        await self._call({"op": "write", "id": point.id, "value": float(value)})

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None


#: registry of available field-bus protocols (config selects by name)
PROTOCOLS = {
    "modbus": {"server": ModbusServer, "client": ModbusClientPlugin, "default_port": 502},
    "simpletcp": {"server": SimpleTcpServer, "client": SimpleTcpClient, "default_port": 7002},
    # real DNP3 / IEC-104 / EtherNet-IP would register here behind the same interfaces
}
