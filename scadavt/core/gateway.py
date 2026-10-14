"""Standalone, domain-neutral gateway (M3).

This is the deployment form of the IOModuleGate: a process that sits *outside*
the emulated SCADA network and moves process I/O between an external application
(over the public Integration API) and the field devices (over the IOModules back
channel). It generalises ``scadavt.gateway.bridge`` — it reuses that module's
validated ``DeviceLink`` (the IOModules client with reconnect) and is driven by a
session descriptor plus the point mapping, with no domain knowledge.

Per cycle: acquire each measurement/state from the external app and write it into
the owning device's back channel; read each command back from the devices and
apply it to the external app. Every exchange is bounded (DeviceLink and
IntegrationClient both have timeouts), so the gateway never blocks indefinitely.
"""

from __future__ import annotations

from .iolink import DeviceLink
from ..util import hostport
from . import integration as ig


class Gateway:
    def __init__(self, app: ig.IntegrationClient, links: dict[str, DeviceLink],
                 mapping: list[dict]):
        self.app = app
        self.links = links
        self.inputs = [m for m in mapping if m.get("role", ig.MEASUREMENT)
                       in (ig.MEASUREMENT, ig.STATE)]
        self.outputs = [m for m in mapping if m.get("role") == ig.COMMAND]
        self.cycles = 0

    @classmethod
    def from_session(cls, session: dict, app: ig.IntegrationClient,
                     timeout: float = 2.0) -> "Gateway":
        links = {}
        for n in session["nodes"]:
            if n.get("backchannel"):
                host, port = hostport(n["backchannel"], 9161)
                links[n["id"]] = DeviceLink(n["id"], host, port, timeout)
        return cls(app, links, session.get("mapping", []))

    async def run_cycle(self) -> dict:
        self.cycles += 1
        rec = {"cycle": self.cycles, "in": 0, "out": 0, "fail": 0}
        # external app -> device inputs (measurements / states)
        for m in self.inputs:
            r = await self.app.acquire(m["point"], m.get("role", ig.MEASUREMENT))
            if r.quality == ig.FAILED:
                rec["fail"] += 1
                continue
            try:
                await self.links[m["device"]].write([(m["point"], r.value)])
                rec["in"] += 1
            except Exception:
                rec["fail"] += 1
                self.links[m["device"]].drop()
        # device outputs (commands) -> external app
        per_dev: dict[str, list[str]] = {}
        for m in self.outputs:
            per_dev.setdefault(m["device"], []).append(m["point"])
        for dev, pids in per_dev.items():
            try:
                vals = await self.links[dev].read(pids)
            except Exception:
                rec["fail"] += 1
                self.links[dev].drop()
                continue
            for pid, v in vals.items():
                if await self.app.control(pid, v):
                    rec["out"] += 1
        return rec

    async def run(self, cycles: int) -> list[dict]:
        return [await self.run_cycle() for _ in range(cycles)]

    async def close(self) -> None:
        for link in self.links.values():
            try:
                await link.close()
            except Exception:
                link.drop()
        await self.app.close()
