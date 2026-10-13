"""Network-disturbance measurement at the communication layer (proposal §8 M1(c)).

Measures what a cyber-disturbance does to SCADA *communication* — round-trip
time and the rate of failed/timed-out transactions — rather than assuming it
must perturb the physical process. A tank-level (or any process) deviation is a
possible downstream consequence, never the acceptance criterion.

Here the disturbance is injected over loopback by a small forwarding proxy that
adds delay and drops data with a probability, standing in for CORE's per-link
``netem`` delay/loss on a capable host. The metric code is identical either way;
only the source of the impairment differs.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from .fieldbus import DevicePoint, ModbusClientPlugin


class DegradedProxy:
    """TCP proxy adding ``delay_s`` latency and dropping data with ``loss``
    probability in each direction (a dropped chunk makes the client time out)."""

    def __init__(self, target_host: str, target_port: int,
                 delay_s: float = 0.0, loss: float = 0.0, seed: int = 0):
        self.target = (target_host, target_port)
        self.delay_s, self.loss = delay_s, loss
        self.rng = random.Random(seed)
        self.server = None

    async def _pump(self, reader, writer):
        try:
            while (chunk := await reader.read(4096)):
                if self.delay_s:
                    await asyncio.sleep(self.delay_s)
                if self.loss and self.rng.random() < self.loss:
                    continue  # drop this chunk -> client will time out
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def _handle(self, c_reader, c_writer):
        try:
            s_reader, s_writer = await asyncio.open_connection(*self.target)
        except OSError:
            c_writer.close()
            return
        await asyncio.gather(self._pump(c_reader, s_writer),
                             self._pump(s_reader, c_writer),
                             return_exceptions=True)

    async def start(self, host: str = "127.0.0.1", port: int = 0):
        self.server = await asyncio.start_server(self._handle, host, port)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.server:
            self.server.close()


@dataclass
class NetMetrics:
    n: int = 0
    ok: int = 0
    failed: int = 0
    rtts_ms: list = field(default_factory=list)

    def summary(self) -> dict:
        r = sorted(self.rtts_ms)
        def pct(p):
            return r[min(len(r) - 1, int(p * len(r)))] if r else 0.0
        return {"n": self.n, "ok": self.ok, "failed": self.failed,
                "fail_rate": round(self.failed / self.n, 3) if self.n else 0.0,
                "rtt_ms_mean": round(sum(r) / len(r), 2) if r else 0.0,
                "rtt_ms_p95": round(pct(0.95), 2), "rtt_ms_max": round(max(r), 2) if r else 0.0}


async def measure_reads(host: str, port: int, point: DevicePoint, n: int = 50,
                        timeout: float = 0.5) -> NetMetrics:
    """Issue ``n`` connect+read transactions (the MTU's per-cycle pattern) and
    record RTT and success/failure. Never blocks beyond ``timeout`` per call."""
    m = NetMetrics()
    for _ in range(n):
        m.n += 1
        plugin = ModbusClientPlugin(host, port, timeout=timeout)
        t0 = time.perf_counter()
        try:
            await plugin.read(point)
            m.rtts_ms.append(1000 * (time.perf_counter() - t0))
            m.ok += 1
        except Exception:
            m.failed += 1
        finally:
            await plugin.close()
    return m
