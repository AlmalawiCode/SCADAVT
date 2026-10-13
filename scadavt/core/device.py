"""Configuration-driven field-device node (M2).

This is what a CORE node actually runs. It stands up, for one device described
in a session, the two faces of a field device (as in the thesis):

* a **field-bus server** on the emulated SCADA network (Modbus/TCP by default,
  chosen by the device's ``protocol``), through which a master/peer reads
  measurements and writes commands;
* an **I/O-modules server** on the back channel, through which the gateway
  writes measurements/states in and reads commands out (the IOModules protocol).

It is domain-neutral and built entirely from a node's point list, so the same
runner serves any configuration. Reuses the validated ``protocols.modbus`` and
``protocols.iomodules`` codecs.

    python -m scadavt.core.device --session session.json --node PLC1 \
        --modbus 0.0.0.0:502 --io 0.0.0.0:9161
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
from pathlib import Path

from ..protocols import iomodules as io
from ..util import hostport, setup_logging
from . import fieldbus as fb
from . import integration as ig


def device_from_node(node: dict) -> fb.Device:
    """Build a neutral :class:`Device` from a session node descriptor."""
    points = [fb.DevicePoint(p["id"], p["role"], bool(p.get("is_bool", False)))
              for p in node.get("points", [])]
    return fb.Device(node["id"], points)


class IOModulesServer:
    """Back-channel server: the gateway writes inputs and reads outputs here."""

    def __init__(self, device: fb.Device):
        self.device = device
        self.requests = 0

    def answer(self, m: io.IOMessage) -> io.IOMessage:
        self.requests += 1
        p = self.device.by_id.get(m.pid)
        if p is None:
            return io.IOMessage(0, m.function, m.pid, 0.0)
        is_input = p.role in (ig.MEASUREMENT, ig.STATE)
        if m.function == io.FC_WRITE and is_input:
            self.device.set_input(m.pid, m.data)
            return io.IOMessage(m.transaction, m.function, m.pid, m.data)
        if m.function == io.FC_READ and not is_input:
            v = self.device.get_output(m.pid)
            if v is not None:
                return io.IOMessage(m.transaction, m.function, m.pid, v)
        return io.IOMessage(0, m.function, m.pid, 0.0)  # not ready / wrong direction

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while (m := await io.read_message(reader)) is not None:
                if m.function == io.FC_TERMINATE:
                    break
                writer.write(self.answer(m).pack())
                await writer.drain()
        except (ConnectionError, OSError, struct.error, UnicodeDecodeError):
            pass
        finally:
            writer.close()


async def run_node(node: dict, modbus: tuple[str, int], io_addr: tuple[str, int]):
    """Start both servers for one device node; returns (device, servers)."""
    device = device_from_node(node)
    proto = node.get("protocol", "modbus")
    server_cls = fb.PROTOCOLS[proto]["server"]
    fb_server = await server_cls(device).serve(*modbus)
    io_server = await asyncio.start_server(IOModulesServer(device).serve, *io_addr)
    return device, fb_server, io_server


async def _main(a) -> None:
    log = setup_logging(a.node)
    session = json.loads(Path(a.session).read_text())
    node = next(n for n in session["nodes"] if n["id"] == a.node)
    device, fb_server, io_server = await run_node(
        node, hostport(a.modbus, 502), hostport(a.io, 9161))
    log.info("%s: field-bus %s, I/O modules %s, %d points", a.node,
             fb_server.sockets[0].getsockname(), io_server.sockets[0].getsockname(),
             len(device.points))
    async with fb_server, io_server:
        await asyncio.gather(fb_server.serve_forever(), io_server.serve_forever())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, help="session.json from coregen")
    ap.add_argument("--node", required=True)
    ap.add_argument("--modbus", default="0.0.0.0:502")
    ap.add_argument("--io", default="0.0.0.0:9161")
    asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
