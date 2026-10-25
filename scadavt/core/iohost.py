"""Adapter host — drives a device's registers from external adapters over the
IOModules back channel (register milestone M4). The deployment form of the I/O
mapping: it runs near a field device (in-node or on the back channel), builds the
configured adapters, and each cycle syncs adapter values into the device's
read-only registers and command registers out to actuators — with data quality,
staleness/fail-safe and deterministic ownership (see :mod:`scadavt.core.iosync`).

    python -m scadavt.core.iohost --session session.json --node PLC1 \
        --io 127.0.0.1:9161 --cycles 60 --sleep 0.2
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

from ..protocols import iomodules as io
from ..util import hostport, setup_logging
from .adapters.registry import make_adapter
from .iosync import IOSync


def _recvn(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("io back channel closed")
        buf += chunk
    return buf


class BackchannelSink:
    """IOSync sink backed by a device's IOModules back channel (one device)."""

    def __init__(self, device_id: str, host: str, port: int, timeout: float = 5.0):
        self.device_id = device_id
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._t = 0

    def _rpc(self, fc: int, pid: str, value: float = 0.0) -> io.IOMessage:
        self._t += 1
        self.sock.sendall(io.IOMessage(self._t, fc, pid, value).pack())
        return io.IOMessage.unpack(_recvn(self.sock, io.PDU_SIZE))

    def write_in(self, device: str, register: str, value: float) -> None:
        self._rpc(io.FC_WRITE, register, float(value))

    def read_out(self, device: str, register: str):
        r = self._rpc(io.FC_READ, register)
        return r.data if r.transaction != 0 else None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--node", required=True, help="the device this host drives")
    ap.add_argument("--io", default="127.0.0.1:9161", help="the device's IOModules back channel")
    ap.add_argument("--cycles", type=int, default=0, help="0 = run until stopped")
    ap.add_argument("--sleep", type=float, default=0.2)
    a = ap.parse_args(argv)
    log = setup_logging(f"iohost:{a.node}")
    session = json.loads(Path(a.session).read_text())

    io_map = [m for m in session.get("io_map", []) if m.get("device") == a.node]
    used = {m.get("adapter") for m in io_map}          # only this node's adapters
    adapters = {ad["id"]: make_adapter(ad) for ad in session.get("adapters", [])
                if ad["id"] in used}
    host, port = hostport(a.io, 9161)
    sink = BackchannelSink(a.node, host, port)
    owners = {(d["id"], r["id"]): r["owner"]
              for d in session.get("nodes", []) for r in d.get("registers", [])
              if d.get("id") == a.node and r.get("owner")}
    sync = IOSync(adapters, io_map, sink, owners=owners)
    log.info("iohost %s: %d adapters, %d io_map entries, back channel %s:%s",
             a.node, len(adapters), len(io_map), host, port)
    outs = [(m["adapter"], m["external"].get("var"), m["register"])
            for m in io_map if m.get("direction") == "out"]
    applied: dict = {}
    i = 0
    try:
        with sync:
            while a.cycles == 0 or i < a.cycles:
                sync.cycle()
                # log each command an emulated actuator receives (on change)
                for ad, var, reg in outs:
                    get = getattr(adapters.get(ad), "output", None)
                    v = get(var) if get else None
                    if v is not None and applied.get(ad) != v:
                        applied[ad] = v
                        log.info("actuator %s <- %s (from %s.%s)", ad, v, a.node, reg)
                i += 1
                time.sleep(a.sleep)
    finally:
        sink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
