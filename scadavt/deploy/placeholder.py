"""Placeholder node used to validate a deployment before the SCADA programs exist.

It opens the TCP ports that the real program would open, accepts connections
and echoes whatever it receives. It carries no SCADA logic.

    python -m scadavt.deploy.placeholder --name PLC1 --ports 502,9161
"""

from __future__ import annotations

import argparse
import asyncio

from ..util import setup_logging


async def echo(reader, writer):
    try:
        while data := await reader.read(4096):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def run(name: str, ports: list[int]):
    log = setup_logging(name)
    servers = [await asyncio.start_server(echo, "0.0.0.0", p) for p in ports]
    log.info("placeholder %s listening on %s", name, ports or "no ports")
    await asyncio.Event().wait()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--ports", default="")
    a = ap.parse_args(argv)
    ports = [int(x) for x in a.ports.split(",") if x.strip()]
    asyncio.run(run(a.name, ports))


if __name__ == "__main__":
    main()
