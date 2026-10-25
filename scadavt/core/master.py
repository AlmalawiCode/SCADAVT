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


#: comparison operators for register-based logic conditions
_OPS = {
    "below": lambda v, t: v < t, "above": lambda v, t: v > t,
    "at_most": lambda v, t: v <= t, "at_least": lambda v, t: v >= t,
    "equal": lambda v, t: v == t, "not_equal": lambda v, t: v != t,
}


def logic_reads(logic: list) -> set:
    """The ``(device, register)`` pairs a logic program reads (its conditions)."""
    return {(c["device"], c["register"]) for rule in logic for c in rule.get("if", [])}


def evaluate_logic(logic: list, values: dict) -> list:
    """Pure evaluator: given logic rules and a ``{(device,register): value}``
    read snapshot, return the ``(device, register, value)`` writes whose
    conditions **all** hold. The central MTU performs the reads/writes over real
    Modbus; this decides what to write."""
    writes = []
    for rule in logic:
        ok = True
        for c in rule.get("if", []):
            v = values.get((c["device"], c["register"]))
            op = _OPS.get(c.get("op", "equal"))
            if v is None or op is None or not op(v, c["value"]):
                ok = False
                break
        if ok:
            for a in rule.get("then", []):
                writes.append((a["device"], a["register"], a.get("set", a.get("value"))))
    return writes


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
    logic = session.get("logic", [])                 # register-based control
    lreads = logic_reads(logic)
    if log:
        log.info("master: %d device links %s, %d measurements, %d commands, "
                 "%d rules, %d logic", len(clients),
                 {k: f"{h}:{p}" for k, (h, p) in ports.items()},
                 len(meas), len(cmd_of), len(session.get("rules", [])), len(logic))
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
            commanded = {}
            for c in engine.evaluate(read, sim_h):      # DECIDE, then WRITE command registers
                tgt = cmd_of.get(c["comp"])
                if tgt is None:
                    continue
                commanded[c["comp"]] = c["value"]       # the control DECISION (commanded stage)
                dev_id, point = tgt
                try:
                    await clients[dev_id].write(point, c["value"])
                except Exception:
                    pass
            if log and commanded:                        # independent record of the commanded stage
                log.info("CMD t=%d %s", i, json.dumps(commanded))

            # register-based control: read condition registers, decide, write
            # target registers — all over real Modbus, node to node (central MTU).
            # Reads are taken FRESH each cycle; a failed read is omitted so its
            # rule cannot fire (never act on invalid/stale data). Conflicting
            # writes resolve deterministically: the last matching rule wins.
            if logic:
                rvals = {}
                for did, rid in lreads:
                    dev = devs.get(did)
                    pt = dev.by_id.get(rid) if dev else None
                    if pt is None:
                        continue
                    try:
                        rvals[(did, rid)] = await clients[did].read(pt)
                    except Exception:
                        pass                     # failed read -> value absent -> rule won't fire
                decided = {}
                for did, rid, val in evaluate_logic(logic, rvals):
                    decided[(did, rid)] = float(val)   # last writer wins (deterministic)
                lcmd = {}
                for (did, rid), val in decided.items():
                    dev = devs.get(did)
                    pt = dev.by_id.get(rid) if dev else None
                    if pt is None:
                        continue
                    owner = getattr(pt, "owner", "")
                    if owner and owner not in ("mtu", "any"):
                        continue                 # register owned elsewhere; MTU may not write it
                    try:
                        await clients[did].write(pt, val)
                        lcmd[f"{did}.{rid}"] = val
                    except Exception:
                        pass
                if log and lcmd:
                    log.info("LOGIC t=%d %s", i, json.dumps(lcmd))
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
