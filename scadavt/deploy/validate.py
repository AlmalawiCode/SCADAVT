"""Check the paths of a deployment from inside a container.

    docker compose exec mtu python -m scadavt.deploy.validate --side mtu
    docker compose exec gateway python -m scadavt.deploy.validate --side gateway

From the MTU it opens a TCP connection to the Modbus port of every PLC across
the routed SCADA network. From the gateway it opens a connection to the
I/O-modules port of every PLC on the back channel and to the WaterSystem
server. For each path it records the connection time and the round-trip time
of small echoed messages (the placeholder nodes echo what they receive), and
writes a CSV file to /logs.
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
import csv
import statistics
import time
from pathlib import Path

from ..protocols import wdsproto as wp


async def probe(name, host, port, n, timeout, echo=True):
    row = {"target": name, "host": host, "port": port, "ok": 0, "connect_ms": None,
           "rtt_ms_median": None, "rtt_ms_p95": None, "error": ""}
    try:
        t0 = time.perf_counter()
        r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        row["connect_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        rtts = []
        for i in range(n):
            if echo:
                msg = i.to_bytes(4, "big") * 4
                t0 = time.perf_counter()
                w.write(msg)
                await w.drain()
                await asyncio.wait_for(r.readexactly(len(msg)), timeout)
            else:  # WaterSystem server: one acquisition request
                t0 = time.perf_counter()
                w.write(wp.WDSMessage("SIMCLOCK", wp.FC_ACQUIRE, 0, 0).pack())
                await w.drain()
                await asyncio.wait_for(r.readexactly(wp.PDU_SIZE), timeout)
            rtts.append((time.perf_counter() - t0) * 1000)
        w.close()
        rtts.sort()
        row["rtt_ms_median"] = round(statistics.median(rtts), 3)
        row["rtt_ms_p95"] = round(rtts[int(0.95 * (len(rtts) - 1))], 3)
        row["ok"] = 1
    except Exception as e:
        row["error"] = type(e).__name__
    return row


async def run(a):
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    if a.side == "mtu":
        cfg.read("/config/mtu_plcs.ini")
        targets = [(s, cfg[s]["ip"], int(cfg[s]["port"]), True) for s in cfg.sections()]
    else:
        cfg.read("/config/iomodulegate_plcs.ini")
        targets = [(s, cfg[s]["ip"], int(cfg[s]["port"]), True) for s in cfg.sections()]
        targets.append(("WDS", a.wds_host, a.wds_port, False))
    rows = await asyncio.gather(*(probe(n, h, p, a.n, a.timeout, e) for n, h, p, e in targets))
    out = Path(a.out or f"/logs/validate_{a.side}.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    ok = sum(r["ok"] for r in rows)
    med = [r["rtt_ms_median"] for r in rows if r["ok"]]
    print(f"{a.side}: {ok}/{len(rows)} paths ok; median RTT "
          f"{statistics.median(med) if med else float('nan'):.3f} ms -> {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--side", choices=["mtu", "gateway"], required=True)
    ap.add_argument("--n", type=int, default=50, help="echo round trips per path")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--wds-host", default="172.16.0.20")
    ap.add_argument("--wds-port", type=int, default=9000)
    ap.add_argument("--out")
    asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
