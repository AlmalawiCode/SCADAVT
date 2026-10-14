"""Domain-neutral IOModules device link (the gateway's back-channel client).

One IOModules session with a field device: read a point, write a point,
reconnect after an error. It speaks only the generic IOModules protocol
(``scadavt.protocols.iomodules``) and carries no domain knowledge, so the
generic gateway can use it without importing any application (e.g. water).

Extracted from the legacy ``scadavt.gateway.bridge`` so that ``scadavt.core``
stays independent of the optional Water application.
"""

from __future__ import annotations

import asyncio

from ..protocols import iomodules as io


class DeviceLink:
    """One IOModules session with a field device; reconnects after an error."""

    def __init__(self, name: str, host: str, port: int, timeout: float):
        self.name, self.host, self.port, self.timeout = name, host, port, timeout
        self.r = self.w = None
        self.counters = {io.FC_READ: 0, io.FC_WRITE: 0}

    async def _call(self, fc: int, pid: str, value: float = 0.0) -> io.IOMessage:
        if self.w is None:
            self.r, self.w = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout)
        self.counters[fc] = self.counters[fc] % 0xFFFFFFFF + 1
        self.w.write(io.IOMessage(self.counters[fc], fc, pid, value).pack())
        await self.w.drain()
        reply = await asyncio.wait_for(io.read_message(self.r), self.timeout)
        if reply is None:
            raise ConnectionError(f"{self.name} closed the I/O session")
        return reply

    def drop(self) -> None:
        if self.w is not None:
            self.w.close()
        self.r = self.w = None

    async def write(self, items: list[tuple[str, float]]) -> int:
        n = 0
        for pid, value in items:
            r = await self._call(io.FC_WRITE, pid, value)
            n += r.transaction == self.counters[io.FC_WRITE]
        return n

    async def read(self, pids: list[str]) -> dict[str, float]:
        out = {}
        for pid in pids:
            r = await self._call(io.FC_READ, pid)
            if r.transaction != 0:
                out[pid] = r.data
        return out

    async def close(self) -> None:
        if self.w is not None:
            self.w.write(io.IOMessage(0, io.FC_TERMINATE, "", 0.0).pack())
            await self.w.drain()
            self.drop()
