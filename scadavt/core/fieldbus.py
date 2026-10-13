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
        c = {"A": 0, "H": 0, "D": 0, "C": 0}
        for p in self.points:
            is_input = p.role in (ig.MEASUREMENT, ig.STATE)
            if p.is_bool:
                p.reg_type = "D" if is_input else "C"
            else:
                p.reg_type = "A" if is_input else "H"
            p.address = c[p.reg_type]
            c[p.reg_type] += 1 if p.is_bool else 2

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


#: registry of available field-bus protocols (config selects by name)
PROTOCOLS = {
    "modbus": {"server": ModbusServer, "client": ModbusClientPlugin, "default_port": 502},
    # "dnp3": {...}, "iec104": {...}  -> future plugins, same interfaces
}
