"""Compatibility adapter for the thesis's binary WaterSystem protocol.

The public Integration API (``integration.py``) is extensible and transport-
neutral. The thesis's fixed 17-byte PDU — ``processID(10s) · op · paraType ·
dataType · value(f32)`` (Fig. 3.3) — is preserved ONLY here, so the original
VB6 WaterSystem server and the existing repository code keep working unchanged.
Its 10-byte identifier and 1-byte type limits stay inside this adapter and never
constrain the public API.

This reuses the validated codec ``scadavt.protocols.wdsproto`` verbatim.
"""

from __future__ import annotations

import asyncio

from ..protocols import wdsproto as wp
from . import integration as ig


class LegacyBinaryServer:
    """Serve an :class:`~scadavt.core.integration.ExternalApp` over the thesis's
    binary PDU, for legacy clients/gateways that speak it."""

    def __init__(self, app: ig.ExternalApp, role_of: dict[str, int] | None = None):
        self.app = app
        # map a legacy processID to the public point id (identity by default)
        self.role_of = role_of or {}

    def _answer(self, m: wp.WDSMessage) -> wp.WDSMessage:
        try:
            if m.function == wp.FC_ACQUIRE:
                role = ig.STATE if m.ptype == wp.PT_LINK else ig.MEASUREMENT
                v = self.app.acquire(m.pid, role)
                return wp.WDSMessage(m.pid, wp.FC_ACQUIRE, m.ptype, m.dtype, float(v))
            if m.function == wp.FC_CONTROL:
                self.app.control(m.pid, m.data)
                return wp.WDSMessage(m.pid, wp.FC_CONTROL, m.ptype, m.dtype, m.data)
        except Exception:
            pass
        return wp.WDSMessage(m.pid, wp.FC_ERROR, m.ptype, m.dtype, 0.0)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                raw = await reader.readexactly(wp.PDU_SIZE)
                writer.write(self._answer(wp.WDSMessage.unpack(raw)).pack())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> asyncio.AbstractServer:
        return await asyncio.start_server(self._serve, host, port)
