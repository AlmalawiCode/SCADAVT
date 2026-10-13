"""Event-stepped timing loop and the end-to-end runner (proposal V6, §5.8).

The runner closes one control loop per cycle over the domain-neutral pieces:

    external app --(Integration API)--> device inputs --> control host -->
    device outputs --(Integration API)--> external app --> step

Two guarantees from the proposal are enforced here:

* **No indefinite blocking.** Every exchange with the external application or
  with a device over the (emulated) network is bounded by a timeout; a missing,
  slow or failed value does not stall the loop — the cycle proceeds and the
  value is tagged with its quality (OK / STALE / FAILED).
* **Reproducibility is not strict determinism.** In the event-stepped mode the
  process only advances after the cycle's commands are applied, which makes the
  *logical* trajectory reproducible for the decoupled layers; it does NOT make
  CORE's wall-clock timing identical. Reproducibility is therefore something to
  measure (repeat runs, compare), not to assume.

The control *placement* is expressed by the transport: a ``NetworkTransport``
puts an MTU/field-bus client between the host and the devices (centralized),
while a ``LocalTransport`` lets a device's own host read/write its image
directly (local PLC control, no MTU).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..protocols import modbus as mb
from . import integration as ig
from .control import ControlHost, dict_reader
from .fieldbus import Device, ModbusClientPlugin


# ------------------------------------------------------------------ transports
class LocalTransport:
    """Local PLC placement: read/write the device image directly (no network)."""
    def __init__(self, devices: dict[str, Device]):
        self.devices = devices

    async def read_input(self, dev_id: str, point_id: str) -> float | None:
        d = self.devices[dev_id]
        p = d.by_id[point_id]
        t = d.bank.tables
        if p.is_bool:
            return float(t["D"][p.address])
        return mb.regs_to_float(t["A"][p.address:p.address + 2])

    async def write_command(self, dev_id: str, point_id: str, value: float) -> bool:
        self.devices[dev_id].set_output(point_id, value)
        return True

    async def close(self): ...


class NetworkTransport:
    """Centralized (MTU) placement: read/write device registers over the
    emulated SCADA network via a field-bus client (the attackable path)."""
    def __init__(self, devices: dict[str, Device], clients: dict[str, ModbusClientPlugin]):
        self.devices = devices
        self.clients = clients

    async def read_input(self, dev_id: str, point_id: str) -> float | None:
        try:
            return await self.clients[dev_id].read(self.devices[dev_id].by_id[point_id])
        except Exception:
            return None

    async def write_command(self, dev_id: str, point_id: str, value: float) -> bool:
        try:
            await self.clients[dev_id].write(self.devices[dev_id].by_id[point_id], value)
            return True
        except Exception:
            return False

    async def close(self):
        for c in self.clients.values():
            await c.close()


# ------------------------------------------------------------------ bindings
@dataclass
class Binding:
    """How one Integration-API point maps to a device point and, for a
    measurement feeding control, to a ``(kind, comp, attr)`` rule key."""
    point: str
    device: str
    role: str                       # ig.MEASUREMENT | ig.COMMAND | ig.STATE
    key: tuple | None = None        # (kind, comp, attr) for control reads
    comp: str | None = None         # actuator comp name (for COMMAND points)


@dataclass
class CycleRecord:
    cycle: int
    sim_time: float
    qualities: dict = field(default_factory=dict)   # point -> OK/STALE/FAILED
    commands: dict = field(default_factory=dict)     # (device, point) -> value
    late: bool = False


class Runner:
    def __init__(self, app: ig.IntegrationClient, devices: dict[str, Device],
                 control: ControlHost, bindings: list[Binding], transport,
                 step_seconds: float = 1.0):
        self.app, self.devices, self.control = app, devices, control
        self.bindings = bindings
        self.transport = transport
        self.step_seconds = step_seconds
        self.cycle = 0
        self.inputs = [b for b in bindings if b.role in (ig.MEASUREMENT, ig.STATE)]
        self.commands = {b.comp: b for b in bindings if b.role == ig.COMMAND}

    async def run_cycle(self) -> CycleRecord:
        self.cycle += 1
        rec = CycleRecord(self.cycle, 0.0)

        # 1. pull measurements/states from the external app (bounded) into device inputs
        for b in self.inputs:
            r = await self.app.acquire(b.point, b.role)
            rec.qualities[b.point] = r.quality
            if r.quality != ig.FAILED:
                self.devices[b.device].set_input(b.point, r.value)

        # 2. build the control read snapshot from the device image (what SCADA sees)
        values = {}
        for b in self.inputs:
            if b.key is not None:
                v = await self.transport.read_input(b.device, b.point)
                if v is not None:
                    values[b.key] = v
        sim_h = (await self.app.clock() or 0.0) / 3600.0

        # 3. decide and 4. apply commands via the placement's transport
        for cmd in self.control.decide(dict_reader(values), sim_h):
            b = self.commands.get(cmd["comp"])
            if b is None:
                continue
            ok = await self.transport.write_command(b.device, b.point, cmd["value"])
            rec.commands[(b.device, b.point)] = cmd["value"]
            if not ok:
                rec.qualities[b.point] = ig.FAILED

        # 5. read commands back from devices and actuate the external app (bounded)
        for b in self.commands.values():
            out = self.devices[b.device].get_output(b.point)
            if out is not None:
                if not await self.app.control(b.point, out):
                    rec.qualities[b.point] = ig.FAILED

        # 6. advance the process one step (event-stepped); bounded, never blocks
        t = await self.app.step(self.step_seconds)
        rec.sim_time = t if t is not None else 0.0
        return rec

    async def run(self, cycles: int) -> list[CycleRecord]:
        return [await self.run_cycle() for _ in range(cycles)]
