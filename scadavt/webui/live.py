"""Run manager for the process-level live mode (no containers).

A run launches the validated WaterSystem server (``scadavt.wds.server``) as a
subprocess — the authoritative plant and simulated clock — and, in-process,
drives it with the validated supervisory logic (``scadavt.master.control.
Supervisor``) over the real WDS protocol, exactly as the offline reference loop
does but in real time. The ground-truth log the server writes is tailed and
broadcast to connected WebSocket clients as live frames.

This mode is scientifically equivalent to ``scadavt.experiments.offline`` and
needs no Docker, so the GUI container stays self-contained. The networked
(Modbus/TCP, container) mode used for in-traffic attacks is added in a later
phase and reuses ``scadavt.experiments.networked``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import socket
import sys
import time
from pathlib import Path

from .. import scenario as sc
from ..master.control import Supervisor
from ..wds.client import WDSClient
from ..wds.model import default_series_path

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "runs"


def _ensure_consumption() -> None:
    """The plant needs the (non-redistributed) consumption series; fetch it once.

    Raises a clear, actionable error if it is missing and cannot be downloaded
    (e.g. offline), so the GUI can tell the researcher how to supply it.
    """
    path = default_series_path()
    if path.exists() and path.stat().st_size > 0:
        return
    tool = ROOT / "tools" / "fetch_consumption.py"
    try:
        import subprocess
        subprocess.run([sys.executable, str(tool), "--out", str(path)],
                       cwd=str(ROOT), check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as e:
        raise RuntimeError(
            f"consumption series not found at {path} and could not be fetched "
            f"({type(e).__name__}). Provide it by running "
            f"`python tools/fetch_consumption.py` with internet access, or mount "
            f"a copy into the container's /app/data directory."
        )
    if not (path.exists() and path.stat().st_size > 0):
        raise RuntimeError(f"consumption series still missing at {path}")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _wait_port(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r, w = await asyncio.open_connection(host, port)
            w.close()
            return True
        except OSError:
            await asyncio.sleep(0.2)
    return False


class RunManager:
    """Owns at most one live run (single-user research tool)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.proc: asyncio.subprocess.Process | None = None
        self.params: dict | None = None
        self.run_id: str | None = None
        self.rundir: Path | None = None
        self.started_at: float | None = None
        self.port: int | None = None
        self._tasks: list[asyncio.Task] = []
        self._clients: set = set()
        self.header: list[str] = []
        self.latest: dict | None = None
        self.error: str | None = None

    # ------------------------------------------------------------- lifecycle
    async def start(self, params: dict) -> dict:
        async with self._lock:
            if self.proc is not None:
                raise RuntimeError("a run is already active; stop it first")
            self.error = None
            await asyncio.get_running_loop().run_in_executor(None, _ensure_consumption)
            p = {
                "seed": int(params.get("seed", 1)),
                "hours": float(params.get("hours", 24)),
                "step": int(params.get("step", 300)),
                "tick": float(params.get("tick", 1.0)),
                "start": str(params.get("start", "2025-01-23")),
                "supervise": bool(params.get("supervise", True)),
                "v1_control": bool(params.get("v1_control", True)),
                "scenario": params.get("scenario") or str(sc.DEFAULT_SCENARIO),
            }
            self.params = p
            self.run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.rundir = RUNS_DIR / self.run_id
            self.rundir.mkdir(parents=True, exist_ok=True)
            gt = self.rundir / "ground_truth.csv"
            self.port = _free_port()

            cmd = [
                sys.executable, "-m", "scadavt.wds.server",
                "--listen", f"127.0.0.1:{self.port}",
                "--start", p["start"], "--seed", str(p["seed"]),
                "--step", str(p["step"]), "--tick", str(p["tick"]),
                "--hours", str(p["hours"]), "--linger", "2.0",
                "--log", str(gt), "--scenario", p["scenario"],
            ]
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(ROOT),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            if not await _wait_port("127.0.0.1", self.port):
                await self._kill()
                raise RuntimeError("WaterSystem server did not start")

            self.started_at = time.time()
            self.header = []
            self.latest = None
            self._tasks = [asyncio.create_task(self._tail(gt))]
            if p["supervise"]:
                self._tasks.append(asyncio.create_task(self._supervise()))
            self._tasks.append(asyncio.create_task(self._watch()))
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            await self._kill()
            return self.status()

    async def _kill(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        if self.proc is not None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
        self.proc = None
        await self._broadcast({"type": "status", "state": "stopped"})

    def status(self) -> dict:
        running = self.proc is not None and self.proc.returncode is None
        return {
            "running": running,
            "run_id": self.run_id,
            "params": self.params,
            "port": self.port,
            "sim_h": (self.latest or {}).get("sim_h"),
            "error": self.error,
            "rundir": str(self.rundir) if self.rundir else None,
        }

    # -------------------------------------------------------------- clients
    def register(self, ws) -> None:
        self._clients.add(ws)

    def unregister(self, ws) -> None:
        self._clients.discard(ws)

    async def _broadcast(self, msg: dict) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    # ----------------------------------------------------------------- tasks
    async def _watch(self) -> None:
        """Detect the server exiting on its own (finished hours / crash)."""
        assert self.proc is not None
        rc = await self.proc.wait()
        if rc not in (0, None) and not self._shutting_down():
            err = b""
            if self.proc.stderr is not None:
                try:
                    err = await self.proc.stderr.read()
                except Exception:
                    pass
            self.error = (err.decode(errors="replace")[-500:] or f"server exited ({rc})")
        await self._broadcast({"type": "status", "state": "finished",
                               "sim_h": (self.latest or {}).get("sim_h")})

    def _shutting_down(self) -> bool:
        return self.proc is None

    async def _tail(self, path: Path) -> None:
        """Tail the ground-truth CSV and broadcast each new row as a frame."""
        pos = 0
        while not path.exists():
            await asyncio.sleep(0.1)
        while True:
            try:
                with open(path, "r") as fh:
                    fh.seek(pos)
                    new = fh.readlines()
                    pos = fh.tell()
            except FileNotFoundError:
                await asyncio.sleep(0.2)
                continue
            for line in new:
                line = line.rstrip("\n")
                if not line:
                    continue
                cells = line.split(",")
                if not self.header:
                    self.header = cells
                    continue
                row = {}
                for k, v in zip(self.header, cells):
                    try:
                        row[k] = float(v)
                    except ValueError:
                        row[k] = v
                self.latest = row
                await self._broadcast({"type": "frame",
                                       "sim_h": row.get("sim_h"), "row": row})
            await asyncio.sleep(0.25)

    async def _supervise(self) -> None:
        """Drive the plant with the validated Supervisor over the WDS protocol."""
        p = self.params
        scen = sc.load(p["scenario"])
        sup = Supervisor(scen.control, scen.doc["tank_max_level"],
                         v1_control=p["v1_control"])
        tags = {(t.plc, t.name): t for t in scen.all_tags()}
        client = WDSClient("127.0.0.1", self.port)
        await client.connect()
        try:
            while True:
                values = {}
                for key in sup.required_reads():
                    t = tags[key]
                    try:
                        values[key] = await client.acquire(t.element, t.ptype, t.dtype)
                    except Exception:
                        pass
                if values:
                    for cmd in sup.decide(values):
                        t = tags[(cmd.plc, cmd.tag)]
                        try:
                            await client.control(t.element, t.ptype, t.dtype, cmd.value)
                        except Exception:
                            pass
                await asyncio.sleep(p["tick"])
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await client.close()
            except Exception:
                pass


manager = RunManager()
