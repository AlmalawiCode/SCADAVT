"""One-shot Modbus client: read or write a device point over the SCADA network.

The hands-on tool for checking node-to-node communication (and for the live
CORE integration test): from one node, read a measurement register or write a
command register on another node, addressing it by name via the session's
register map — no raw Modbus offsets to remember.

    # read a point (measurement or command) from a device at its SCADA IP
    python -m scadavt.core.probe --session session.json --node PLC1 --point lvl
    # write a command point
    python -m scadavt.core.probe --session session.json --node PLC1 --point pmp --write 1

Exit code 0 on success (prints the value); non-zero on failure (timeout /
connection refused / unknown point), so a test can assert on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import fieldbus as fb


def _device_and_addr(session: dict, node_id: str):
    node = next((n for n in session.get("nodes", []) if n["id"] == node_id), None)
    if node is None:
        raise SystemExit(f"no such node {node_id!r} in session")
    from .device import device_from_node
    dev = device_from_node(node)
    proto = node.get("protocol", "modbus")
    port = int(node.get("port") or fb.PROTOCOLS[proto]["default_port"])
    host = node.get("ip") or "127.0.0.1"
    return dev, proto, host, port


async def _run(a) -> int:
    session = json.loads(Path(a.session).read_text())
    dev, proto, host, port = _device_and_addr(session, a.node)
    host = a.host or host
    port = a.port or port
    if a.point not in dev.by_id:
        print(f"unknown point {a.point!r} on {a.node}", file=sys.stderr)
        return 2
    point = dev.by_id[a.point]
    client = fb.PROTOCOLS[proto]["client"](host, port, timeout=a.timeout)
    try:
        if a.write is not None:
            await client.write(point, float(a.write))
            print(f"wrote {a.node}.{a.point} = {a.write}  @ {host}:{port}")
        else:
            val = await client.read(point)
            print(f"{a.node}.{a.point} = {val}  @ {host}:{port}")
        return 0
    except Exception as e:
        print(f"probe failed ({host}:{port}): {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        await client.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--node", required=True, help="device node to talk to")
    ap.add_argument("--point", required=True, help="point id (addressed via the register map)")
    ap.add_argument("--write", default=None, help="value to write (omit to read)")
    ap.add_argument("--host", default=None, help="override the node's SCADA IP")
    ap.add_argument("--port", type=int, default=None, help="override the Modbus port")
    ap.add_argument("--timeout", type=float, default=2.0)
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
