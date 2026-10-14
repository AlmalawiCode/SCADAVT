"""Run the gateway as a standalone process (the deployment form of IOModuleGate).

It sits OUTSIDE the emulated SCADA network, on the out-of-band back channel, and
each cycle: pulls every measurement/state from the external application (over the
Integration API) into the owning device's registers, pushes device command
registers back to the application, and then advances the physical clock one step
(STEP). The MTU/master drives control independently over the SCADA network, so
this process only bridges the physical process and the field devices.

    python -m scadavt.core.gateway_run --session session.json \
        --app 172.16.0.254:9000 --step 120 --cycles 60

With ``--cycles 0`` it runs until stopped. ``--sleep`` paces wall-clock cycles.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..util import hostport, setup_logging
from . import integration as ig
from .gateway import Gateway


async def run_gateway(session: dict, app_host: str, app_port: int, step_seconds: float,
                      cycles: int = 0, sleep: float = 0.0, log=None):
    client = ig.IntegrationClient(app_host, app_port)
    hello = await client.hello()
    if log:
        log.info("gateway: app %s:%s hello=%s", app_host, app_port, hello.get("ok"))
    gw = Gateway.from_session(session, client)
    i = 0
    try:
        while cycles == 0 or i < cycles:
            rec = await gw.run_cycle()               # app <-> device registers
            await client.step(step_seconds)          # advance the physical process
            if log and (i % 10 == 0):
                log.info("gateway cycle %d: in=%d out=%d fail=%d", i,
                         rec["in"], rec["out"], rec["fail"])
            i += 1
            if sleep:
                await asyncio.sleep(sleep)
        return i
    finally:
        await gw.close()


async def _main(a) -> None:
    log = setup_logging("gateway")
    session = json.loads(Path(a.session).read_text())
    host, port = hostport(a.app, 9000)
    await run_gateway(session, host, port, a.step, a.cycles, a.sleep, log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, help="session.json (nodes with backchannel + mapping)")
    ap.add_argument("--app", required=True, help="external app HOST:PORT (Integration API)")
    ap.add_argument("--step", type=float, default=120.0, help="seconds to advance per cycle")
    ap.add_argument("--cycles", type=int, default=0, help="stop after N cycles (0 = forever)")
    ap.add_argument("--sleep", type=float, default=0.0, help="wall-clock pause between cycles")
    asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
