"""The public Integration API: the documented boundary between SCADAVT and the
independent external applications that simulate a physical process.

Design goals (proposal V6, §4.4):

* **Extensible, not limited by the thesis wire format.** Identifiers are
  arbitrary-length strings; data types are an open registry of ``{name, unit}``;
  values are typed (number by default). The contract is versioned and
  capability-negotiated (``HELLO``/``LIST``), so new roles, types and features
  can be added without breaking clients. The thesis's 10-byte / 1-byte binary
  PDU is preserved only behind a compatibility adapter (see ``legacy.py``); its
  limits never constrain this public API.
* **Three kinds of data**, as the roles below: a *measurement* a sensor
  produces (app -> SCADAVT), a *command* an actuator receives (SCADAVT -> app),
  and the *actual actuator state* fed back (app -> SCADAVT).
* **Out of band.** This channel is the integration/back channel; it does NOT run
  over the emulated SCADA network, so attacks and capture stay on the SCADA
  traffic and datasets remain representative (§5.4).
* **Never blocks.** Every client call is bounded by a timeout; a missing, slow
  or failed exchange returns quality ``FAILED``/``STALE`` instead of hanging, so
  the timing loop can always make progress (§5.8).

Connection model (as in the thesis): the external application LISTENS (a
server); SCADAVT's gateway CONNECTS (the client). Framing is a 4-byte
big-endian length prefix followed by a JSON object, which keeps the API
transport-neutral and trivially extensible.
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from dataclasses import asdict, dataclass

API_VERSION = 1

# roles
MEASUREMENT = "measurement"   # sensor value, app -> SCADAVT
COMMAND = "command"           # actuator command, SCADAVT -> app
STATE = "state"               # actual actuator state / feedback, app -> SCADAVT
ROLES = (MEASUREMENT, COMMAND, STATE)

# value quality (lets the timing loop proceed without blocking)
OK = "OK"
STALE = "STALE"
FAILED = "FAILED"

_LEN = struct.Struct(">I")


@dataclass
class Point:
    """A process parameter the external application exposes."""
    id: str                 # arbitrary-length identifier (no 10-byte limit)
    role: str               # MEASUREMENT | COMMAND | STATE
    dataType: str = "number"  # open registry entry, app-defined
    unit: str = ""          # e.g. "m", "V", "L/min"; free text


@dataclass
class Reading:
    value: float
    quality: str = OK
    ts: float = 0.0


async def _read_msg(reader: asyncio.StreamReader) -> dict | None:
    try:
        head = await reader.readexactly(_LEN.size)
        (n,) = _LEN.unpack(head)
        body = await reader.readexactly(n)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        return None
    return json.loads(body.decode("utf-8"))


async def _write_msg(writer: asyncio.StreamWriter, msg: dict) -> None:
    body = json.dumps(msg).encode("utf-8")
    writer.write(_LEN.pack(len(body)) + body)
    await writer.drain()


# ------------------------------------------------------------------ server side
class ExternalApp:
    """Base class for an external process application (the server side).

    A concrete simulator (water, power, manufacturing, a test double) subclasses
    this and implements :meth:`points`, :meth:`acquire`, :meth:`control` and,
    optionally, :meth:`step`. SCADAVT never imports the subclass: it only speaks
    the wire protocol, so the application is a genuinely independent program.
    """

    #: advertised, optional feature flags negotiated at HELLO
    features: dict = {"step": False}

    def points(self) -> list[Point]:
        raise NotImplementedError

    def acquire(self, point_id: str, role: str) -> float:
        """Return the current value of a MEASUREMENT or STATE point."""
        raise NotImplementedError

    def control(self, point_id: str, value: float) -> None:
        """Apply a COMMAND to an actuator."""
        raise NotImplementedError

    def clock(self) -> float:
        """Simulated time in seconds (for timestamping / synchronisation)."""
        return time.time()

    def step(self, seconds: float) -> float:
        """Advance the process (event-stepped mode). Default: no-op free-run."""
        return self.clock()

    # -- wire handling -------------------------------------------------------
    def _handle(self, msg: dict) -> dict:
        op = msg.get("op")
        try:
            if op == "HELLO":
                return {"ok": True, "api_version": API_VERSION,
                        "capabilities": ["ACQUIRE", "CONTROL", "LIST", "CLOCK"]
                        + (["STEP"] if self.features.get("step") else []),
                        "features": dict(self.features)}
            if op == "LIST":
                return {"ok": True, "points": [asdict(p) for p in self.points()]}
            if op == "ACQUIRE":
                v = self.acquire(msg["id"], msg.get("role", MEASUREMENT))
                return {"ok": True, "value": float(v), "quality": OK,
                        "ts": self.clock()}
            if op == "CONTROL":
                self.control(msg["id"], float(msg["value"]))
                return {"ok": True, "applied_ts": self.clock()}
            if op == "CLOCK":
                return {"ok": True, "sim_time": self.clock()}
            if op == "STEP":
                return {"ok": True, "sim_time": self.step(float(msg.get("seconds", 0.0)))}
        except KeyError as e:
            return {"ok": False, "error": f"missing field {e}"}
        except Exception as e:  # a bad request must not kill the server
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": False, "error": f"unknown op {op!r}"}

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while (msg := await _read_msg(reader)) is not None:
                await _write_msg(writer, self._handle(msg))
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> asyncio.AbstractServer:
        """Start listening; returns the asyncio server (port 0 picks a free port)."""
        return await asyncio.start_server(self._serve, host, port)


# ------------------------------------------------------------------ client side
class IntegrationClient:
    """The SCADAVT gateway's client. Every call is bounded by ``timeout`` and
    never blocks indefinitely; failures surface as quality ``FAILED``."""

    def __init__(self, host: str, port: int, timeout: float = 2.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = self.writer = None

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None

    async def _call(self, msg: dict) -> dict:
        try:
            if self.writer is None:
                await asyncio.wait_for(self.connect(), self.timeout)
            await asyncio.wait_for(_write_msg(self.writer, msg), self.timeout)
            reply = await asyncio.wait_for(_read_msg(self.reader), self.timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError) as e:
            self.writer = None  # force reconnect next time
            return {"ok": False, "error": f"{type(e).__name__}", "timeout": True}
        if reply is None:
            self.writer = None
            return {"ok": False, "error": "closed"}
        return reply

    async def hello(self) -> dict:
        return await self._call({"op": "HELLO", "client_version": API_VERSION})

    async def list_points(self) -> list[Point]:
        r = await self._call({"op": "LIST"})
        return [Point(**p) for p in r.get("points", [])] if r.get("ok") else []

    async def acquire(self, point_id: str, role: str = MEASUREMENT) -> Reading:
        r = await self._call({"op": "ACQUIRE", "id": point_id, "role": role})
        if r.get("ok"):
            return Reading(float(r["value"]), r.get("quality", OK), r.get("ts", 0.0))
        return Reading(0.0, FAILED, 0.0)

    async def control(self, point_id: str, value: float) -> bool:
        r = await self._call({"op": "CONTROL", "id": point_id, "value": float(value)})
        return bool(r.get("ok"))

    async def clock(self) -> float | None:
        r = await self._call({"op": "CLOCK"})
        return float(r["sim_time"]) if r.get("ok") else None

    async def step(self, seconds: float) -> float | None:
        r = await self._call({"op": "STEP", "seconds": float(seconds)})
        return float(r["sim_time"]) if r.get("ok") else None
