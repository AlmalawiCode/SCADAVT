"""Run an EPANET physical-process application exposing the public Integration API.

This is an **independent external application**, exactly as in the proposal: it
holds the physical model (EPANET) and speaks the documented Integration API; it
listens, and SCADAVT's gateway connects to it as a client. SCADAVT Core imports
nothing from here. To integrate a different simulator (power, gas, manufacturing)
a researcher writes their own app with the same API — SCADAVT is not modified.

    python -m scadavt.examples.app_runner --config lab.json --listen 0.0.0.0:9000

``lab.json`` carries the ``epanet`` design + the point ``mapping`` (as produced
by the lab builder). The app is passive: it answers ACQUIRE/CONTROL/CLOCK and
advances the hydraulics only when the gateway calls STEP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from scadavt.util import hostport
from .design_app import DesignApp


async def _main(a) -> None:
    cfg = json.loads(Path(a.config).read_text())
    app = DesignApp(cfg["epanet"], cfg["mapping"])
    host, port = hostport(a.listen, 9000)
    server = await app.serve(host, port)
    sock = server.sockets[0].getsockname()
    print(f"EPANET app listening on {sock[0]}:{sock[1]}  "
          f"points={len(app.bind)}  (Integration API)", flush=True)
    try:
        async with server:
            await server.serve_forever()
    finally:
        app.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="lab config JSON (epanet design + mapping)")
    ap.add_argument("--listen", default="0.0.0.0:9000", help="HOST:PORT to listen on")
    asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
