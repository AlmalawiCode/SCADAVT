"""Pluggable field-bus layer (proposal V6, §4.5).

A field device is split into a **domain-neutral process image** (named points
holding measurements and commands) and a **pluggable field-bus server** that
exposes that image on the emulated SCADA network. Modbus/TCP is the default,
validated plugin; other protocols (DNP3, IEC-104, EtherNet/IP) can be added
behind the same two interfaces without touching the rest of the core.

The device's protocol is a configuration choice, so devices speaking different
protocols can coexist in one topology. Nothing here is domain-specific: a point
is just ``(role, data kind, bool-or-float)`` with an address.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..protocols import modbus as mb
from . import integration as ig


@dataclass
class DevicePoint:
    id: str
    role: str            # ig.MEASUREMENT (input) | ig.COMMAND (output) | ig.STATE
    is_bool: bool        # discrete (coil/discrete-input) vs float (register pair)
    address: int = 0
    reg_type: str = ""   # 'A' input reg, 'D' discrete in, 'H' holding, 'C' coil
    pinned: bool = False  # True: ``address`` is fixed by configuration, not auto-assigned


# Modbus table <-> role, and the conventional 5-digit reference base of each table.
# A point's TABLE is fixed by its role (the passive-slave convention): a sensor
# reading is an input register / discrete input; an actuator command is a holding
# register / coil. Only the ADDRESS within that table may be pinned in config.
_TABLE_NAME = {"A": "input_register", "D": "discrete_input",
               "H": "holding_register", "C": "coil"}
_REF_BASE = {"C": 1, "D": 10001, "A": 30001, "H": 40001}


def _table_for(role: str, is_bool: bool) -> str:
    is_input = role in (ig.MEASUREMENT, ig.STATE)
    if is_bool:
        return "D" if is_input else "C"
    return "A" if is_input else "H"


class Device:
    """A neutral process image for one field device, plus register assignment.

    Measurements/states are *inputs* the gateway writes and a master reads;
    commands are *outputs* a master writes and the gateway reads back. This is
    the documented passive-slave model (the device holds no control logic).
    """

    def __init__(self, name: str, points: list[DevicePoint]):
        self.name = name
        self.points = points
        self._assign()
        size = {"A": 0, "H": 0, "D": 0, "C": 0}
        for p in points:
            size[p.reg_type] = max(size[p.reg_type], p.address + (1 if p.is_bool else 2))
        self.bank = mb.DataBank(coils=size["C"], discrete=size["D"], holding=size["H"],
                                inputs=size["A"], guard_inputs=True)
        self.by_id = {p.id: p for p in points}

    def _assign(self) -> None:
        """Place each point in its Modbus table. The table is fixed by role; the
        address is honoured when the configuration pinned it, otherwise packed in
        after any pinned ranges, so a real register map can be declared in config
        (sensor -> input register 30001) yet never clashes with auto points."""
        c = {"A": 0, "H": 0, "D": 0, "C": 0}
        for p in self.points:
            p.reg_type = _table_for(p.role, p.is_bool)
            if p.pinned:
                c[p.reg_type] = max(c[p.reg_type], p.address + (1 if p.is_bool else 2))
        for p in self.points:
            if p.pinned:
                continue
            p.address = c[p.reg_type]
            c[p.reg_type] += 1 if p.is_bool else 2

    def register_map(self) -> list[dict]:
        """The device's Modbus register map: where each point actually lives, so a
        configuration (and a human) can see sensor/actuator <-> register exactly as
        on a real PLC. ``ref`` is the conventional 5-digit Modbus reference."""
        rows = []
        for p in self.points:
            width = 1 if p.is_bool else 2
            rows.append({
                "point": p.id, "role": p.role,
                "table": _TABLE_NAME[p.reg_type],
                "access": "read-only" if p.reg_type in "AD" else "read/write",
                "address": p.address, "count": width,
                "ref": _REF_BASE[p.reg_type] + p.address,
                "data_type": "bool" if p.is_bool else "float32",
            })
        return rows

    # -- gateway side: write a measurement/state in, read a command out ------
    def set_input(self, point_id: str, value: float) -> None:
        p = self.by_id[point_id]
        t = self.bank.tables
        if p.is_bool:
            t["D"][p.address] = value >= 0.5
            self.bank.mark_input("D", p.address)
        else:
            t["A"][p.address:p.address + 2] = mb.float_to_regs(value)
            self.bank.mark_input("A", p.address, 2)

    def get_output(self, point_id: str) -> float | None:
        """Last command written (by a master, or locally), or None if never."""
        p = self.by_id[point_id]
        t, w = self.bank.tables, self.bank.written
        if p.is_bool:
            return (1.0 if t["C"][p.address] else 0.0) if ("C", p.address) in w else None
        if ("H", p.address) in w and ("H", p.address + 1) in w:
            return mb.regs_to_float(t["H"][p.address:p.address + 2])
        return None

    def set_output(self, point_id: str, value: float) -> None:
        """Write a command directly into the output table. Used by **local (PLC)
        control**, where the device's own logic actuates without a master."""
        p = self.by_id[point_id]
        t = self.bank.tables
        if p.is_bool:
            t["C"][p.address] = value >= 0.5
            self.bank.written.add(("C", p.address))
        else:
            t["H"][p.address:p.address + 2] = mb.float_to_regs(value)
            self.bank.written.add(("H", p.address))
            self.bank.written.add(("H", p.address + 1))


# ------------------------------------------------------------------ plugins
class FieldbusServer:
    """Interface: expose a device's image on the emulated network."""
    async def serve(self, host: str, port: int): raise NotImplementedError


class FieldbusClient:
    """Interface: a master/peer reading and writing device points over the net."""
    async def read(self, point: DevicePoint) -> float: raise NotImplementedError
    async def write(self, point: DevicePoint, value: float) -> None: raise NotImplementedError
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
    """Default plugin: read/write device points via the validated ModbusClient."""

    def __init__(self, host: str, port: int, unit: int = 1, timeout: float = 2.0):
        self.c = mb.ModbusClient(host, port, unit, timeout=timeout)
        self._open = False

    async def _ensure(self) -> None:
        if not self._open:
            await self.c.connect()
            self._open = True

    async def read(self, point: DevicePoint) -> float:
        await self._ensure()
        if point.is_bool:
            fc = mb.READ_DISCRETE_INPUTS if point.reg_type == "D" else mb.READ_COILS
            return float((await self.c.read_bits(fc, point.address, 1))[0])
        fc = mb.READ_INPUT_REGISTERS if point.reg_type == "A" else mb.READ_HOLDING_REGISTERS
        return mb.regs_to_float(await self.c.read_registers(fc, point.address, 2))

    async def write(self, point: DevicePoint, value: float) -> None:
        await self._ensure()
        if point.is_bool:
            await self.c.write_coil(point.address, value >= 0.5)
        else:
            await self.c.write_registers(point.address, mb.float_to_regs(value))

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
                return {"ok": True, "value": float(t[p.reg_type][p.address])}
            return {"ok": True, "value": mb.regs_to_float(t[p.reg_type][p.address:p.address + 2])}
        if req["op"] == "write":
            if p.is_bool:
                t[p.reg_type][p.address] = req["value"] >= 0.5
                self.device.bank.written.add((p.reg_type, p.address))
            else:
                t[p.reg_type][p.address:p.address + 2] = mb.float_to_regs(req["value"])
                self.device.bank.written.add((p.reg_type, p.address))
                self.device.bank.written.add((p.reg_type, p.address + 1))
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

    async def read(self, point: DevicePoint) -> float:
        return float((await self._call({"op": "read", "id": point.id}))["value"])

    async def write(self, point: DevicePoint, value: float) -> None:
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
