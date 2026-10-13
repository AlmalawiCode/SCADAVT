"""Asyncio client of the WaterSystem server (used by the meta-converter and tests)."""

from __future__ import annotations

import asyncio

from ..protocols import wdsproto as wp


class WDSError(Exception):
    pass


class WDSClient:
    def __init__(self, host: str, port: int = 9000, timeout: float = 5.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = self.writer = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            self.writer = None

    async def _request(self, m: wp.WDSMessage) -> wp.WDSMessage:
        async with self._lock:  # one outstanding request per connection
            self.writer.write(m.pack())
            await self.writer.drain()
            raw = await asyncio.wait_for(self.reader.readexactly(wp.PDU_SIZE), self.timeout)
        r = wp.WDSMessage.unpack(raw)
        if r.function == wp.FC_ERROR:
            raise WDSError(f"request failed: {m}")
        return r

    async def acquire(self, pid: str, ptype: int, dtype: int) -> float:
        return (await self._request(wp.WDSMessage(pid, wp.FC_ACQUIRE, ptype, dtype))).data

    async def control(self, pid: str, ptype: int, dtype: int, value: float) -> float:
        return (await self._request(wp.WDSMessage(pid, wp.FC_CONTROL, ptype, dtype, value))).data

    async def sim_hours(self) -> float:
        return await self.acquire("SIMCLOCK", wp.PT_NODE, 0)
