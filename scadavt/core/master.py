"""Configuration-driven MTU / master node (what a 'master' CORE node runs).

This is the centralized (``mtu``) control placement, on-node: it reads the
generated ``session.json``, opens a field-bus client to each field device's
**SCADA-network IP** (the node's own IP inside CORE), runs the domain-neutral
rule engine against the measurement registers it reads, and writes the command
registers back — over the emulated SCADA network, node to node.

    python -m scadavt.core.master --session session.json --node MTU

It is the peer of :mod:`scadavt.core.device`: the device node serves its
registers, this master node reads/writes them. On a CORE host the master runs
inside the MTU namespace and reaches each PLC by IP; over loopback (no CORE) it
reaches them by 127.0.0.1 — the identical Modbus/TCP stack either way.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from ..builder.rules import RuleEngine
from ..util import setup_logging
from . import fieldbus as fb
from . import integration as ig
from .device import device_from_node


def _links(session: dict):
    """Build a Modbus client to each field device, keyed by device id, plus the
    local register handle for each mapped point. Addresses come from the device's
    own register map, so a pinned/declared map is honoured."""
    devs, clients, ports = {}, {}, {}
    for n in session.get("nodes", []):
        if n.get("role") != "field-device":
            continue
        dev = device_from_node(n)
        devs[n["id"]] = dev
        proto = n.get("protocol", "modbus")
        port = int(n.get("port") or fb.PROTOCOLS[proto]["default_port"])
        host = n.get("ip") or "127.0.0.1"
        clients[n["id"]] = fb.PROTOCOLS[proto]["client"](host, port)
        ports[n["id"]] = (host, port)
    return devs, clients, ports


async def run_master(session: dict, step_seconds: float = 1.0, cycles: int | None = None,
                     log=None):
    devs, clients, ports = _links(session)
    meas, cmd_of = [], {}
    for m in session.get("mapping", []):
        dev = devs.get(m.get("device"))
        if dev is None or m.get("point") not in dev.by_id:
            continue
        comp = m.get("element", m["point"])          # the physical element the register drives/reads
        if m.get("role") == ig.COMMAND:
            cmd_of[comp] = (m["device"], dev.by_id[m["point"]])
        else:
            meas.append((comp, m["device"], dev.by_id[m["point"]]))
    engine = RuleEngine.from_json(session.get("rules", []))
    if log:
        log.info("master: %d device links %s, %d measurements, %d commands, %d rules",
                 len(clients), {k: f"{h}:{p}" for k, (h, p) in ports.items()},
                 len(meas), len(cmd_of), len(session.get("rules", [])))
    t0 = time.time()
    i = 0
    try:
        while cycles is None or i < cycles:
            values = {}
            for comp, dev_id, point in meas:            # READ measurement registers over Modbus
                try:
                    values[comp] = await clients[dev_id].read(point)
                except Exception:
                    pass
            read = lambda kind, comp, attr: values.get(comp)
            sim_h = (time.time() - t0) / 3600.0
            for c in engine.evaluate(read, sim_h):      # DECIDE, then WRITE command registers
                tgt = cmd_of.get(c["comp"])
                if tgt is None:
                    continue
                dev_id, point = tgt
                try:
                    await clients[dev_id].write(point, c["value"])
                except Exception:
                    pass
            i += 1
            if cycles is None or i < cycles:
                await asyncio.sleep(step_seconds)
        return values
    finally:
        for c in clients.values():
            await c.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, help="session.json from coregen")
    ap.add_argument("--node", default="MTU")
    ap.add_argument("--step", type=float, default=1.0, help="seconds between control cycles")
    ap.add_argument("--cycles", type=int, default=None, help="stop after N cycles (default: forever)")
    a = ap.parse_args(argv)
    log = setup_logging(a.node)
    session = json.loads(Path(a.session).read_text())
    asyncio.run(run_master(session, a.step, a.cycles, log=log))


if __name__ == "__main__":
    main()
