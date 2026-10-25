"""I/O synchronisation engine (register milestone M4).

Drives an ``io_map`` each cycle between external adapters and PLC registers,
applying data-quality/staleness, fail-safe behaviour and deterministic write
ownership. It is transport-agnostic via a *sink*: in-process against
``fieldbus.Device`` objects (offline/CI), or against the device back-channel on
live CORE.

``io_map`` entry::

    {"adapter": "sim1", "external": {"var": "temp"},
     "device": "PLC1", "register": "lvl", "direction": "in",
     "scale": 1.0, "offset": 0.0, "max_age": 5.0,
     "quality": "hold_last", "default": 0.0, "owner": "process"}

``direction: in``  — adapter -> register (a producer sets a read-only register).
``direction: out`` — register -> adapter (an actuator applies a command value).
"""

from __future__ import annotations

from .adapters.base import QUALITY_OK, QUALITY_STALE, QUALITY_FAILED
from .adapters.registry import make_adapter
from .ownership import WriteArbiter


def _var(external: dict) -> str:
    return external.get("var") or external.get("element") or external.get("channel")


class DeviceSink:
    """Sink backed by in-memory :class:`fieldbus.Device` objects.

    ``write_in`` sets a producer (read-only) register; ``read_out`` reads the last
    value written to a read/write register."""

    def __init__(self, devices: dict):
        self.devices = devices

    def write_in(self, device: str, register: str, value: float) -> None:
        self.devices[device].set_input(register, value)

    def read_out(self, device: str, register: str):
        return self.devices[device].get_output(register)


class IOSync:
    def __init__(self, adapters: dict, io_map: list, sink, arbiter: WriteArbiter | None = None,
                 owners: dict | None = None):
        self.adapters = adapters                 # id -> Adapter (already constructed)
        self.io_map = list(io_map)
        self.sink = sink
        self.arbiter = arbiter or WriteArbiter()
        self.owners = owners or {}               # (device, register) -> declared owner
        self._last: dict = {}                    # (device, register) -> (value, quality, ts)
        self.quality: dict = {}                  # last quality per (device, register)

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def from_config(cls, cfg: dict, sink, arbiter=None):
        adapters = {a["id"]: make_adapter(a) for a in cfg.get("adapters", [])}
        owners = {(d["id"], r["id"]): r["owner"]
                  for d in cfg.get("devices", []) for r in d.get("registers", [])
                  if r.get("owner")}
        return cls(adapters, cfg.get("io_map", []), sink, arbiter, owners)

    def open(self) -> None:
        for a in self.adapters.values():
            a.open()
            a.start()

    def close(self) -> None:
        for a in self.adapters.values():
            try:
                a.stop()
            finally:
                a.close()                        # always release, even on error

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- one sync cycle ----------------------------------------------------
    def cycle(self) -> None:
        # OUT: register command -> adapter (simulated/real actuator responds)
        for m in self.io_map:
            if m.get("direction") != "out":
                continue
            val = self.sink.read_out(m["device"], m["register"])
            if val is None:
                continue
            a = self.adapters.get(m["adapter"])
            if a is not None:
                try:
                    a.write(_var(m["external"]), float(val))
                except Exception:
                    pass                         # a failing actuator must not crash control

        # IN: adapter measurement -> register, with quality + ownership arbitration
        proposals: dict = {}
        for m in self.io_map:
            if m.get("direction", "in") != "in":
                continue
            key = (m["device"], m["register"])
            value, qual = self._sample(m, key)
            self.quality[key] = qual
            if value is not None:
                proposals.setdefault(key, []).append((m.get("owner", "process"), value))
        for key, props in proposals.items():
            v = self.arbiter.resolve(self.owners.get(key), props)
            if v is not None:
                self.sink.write_in(key[0], key[1], v)

    # -- quality / staleness / fail-safe ----------------------------------
    def _sample(self, m: dict, key):
        var = _var(m["external"])
        a = self.adapters.get(m["adapter"])
        scale, offset = float(m.get("scale", 1.0)), float(m.get("offset", 0.0))
        try:
            s = a.read(var)
        except Exception:
            return self._failsafe(m, key, QUALITY_FAILED)
        qual = s.quality
        max_age = m.get("max_age")
        if qual == QUALITY_OK and max_age is not None and s.age() > float(max_age):
            qual = QUALITY_STALE                 # a successful read can still be STALE
        if qual == QUALITY_OK:
            v = s.value * scale + offset
            self._last[key] = (v, QUALITY_OK, s.ts)
            return v, QUALITY_OK
        return self._failsafe(m, key, qual)

    def _failsafe(self, m: dict, key, qual: str):
        """Never actuate on invalid/stale data: apply the configured policy."""
        policy = m.get("quality", "hold_last")
        if policy == "hold_last":
            last = self._last.get(key)
            return (last[0], qual) if last else (None, qual)   # nothing to hold -> no write
        if policy == "default":
            return float(m.get("default", 0.0)), qual
        return None, qual                        # 'fault' -> do not write
