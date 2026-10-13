"""System readiness checks and one-click image build for the lab.

Lets the GUI tell the researcher whether the host can run containerised attacks
(Docker reachable, the testbed images built, the consumption series present) and
build the images from the browser instead of the command line.
"""

from __future__ import annotations

import asyncio
import subprocess

from ..wds.model import default_series_path
from .live import ROOT

IMAGES = ("scadavt:2.0", "scadavt-net:2.0")


def _ok(cmd: list[str], timeout: float = 8.0) -> bool:
    try:
        return subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              timeout=timeout).returncode == 0
    except Exception:
        return False


def info() -> dict:
    docker = _ok(["docker", "version", "--format", "{{.Server.Version}}"])
    images = {img: (docker and _ok(["docker", "image", "inspect", img])) for img in IMAGES}
    return {
        "docker": docker,
        "compose": _ok(["docker", "compose", "version"]),
        "images": images,
        "images_ready": all(images.values()),
        "consumption": default_series_path().exists(),
        "can_run_attacks": docker and all(images.values()),
    }


class BuildJob:
    """Build the two testbed images in the background (base, then net)."""

    def __init__(self) -> None:
        self.state = "idle"          # idle|building|done|error
        self.step = ""
        self.tail = ""
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None

    def status(self) -> dict:
        return {"state": self.state, "step": self.step, "tail": self.tail[-800:]}

    async def start(self) -> dict:
        if self.state == "building":
            return self.status()
        self.state = "building"
        self.tail = ""
        self._task = asyncio.create_task(self._run())
        return self.status()

    async def _run(self):
        builds = [("scadavt:2.0", ["docker", "build", "-t", "scadavt:2.0", "."]),
                  ("scadavt-net:2.0", ["docker", "build", "-t", "scadavt-net:2.0",
                                       "-f", "deploy/Dockerfile.net", "."])]
        try:
            for name, cmd in builds:
                self.step = f"building {name}"
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(ROOT),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                assert self._proc.stdout is not None
                async for line in self._proc.stdout:
                    self.tail += line.decode(errors="replace")
                    self.tail = self.tail[-4000:]
                rc = await self._proc.wait()
                if rc != 0:
                    self.state = "error"
                    self.step = f"{name} failed (exit {rc})"
                    return
            self.state = "done"
            self.step = "images ready"
        except Exception as e:
            self.state = "error"
            self.step = f"{type(e).__name__}: {e}"


build_job = BuildJob()
