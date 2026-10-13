"""Run attack experiments and build datasets from the browser — no CLI.

The GUI composes an experiment, and this manager launches the networked attack
runner (``scadavt.attacks.runner``) as a background process on the host, tails
its ground-truth and historian logs to stream the true vs. MTU-observed value of
the victim tag live, and, when it finishes, builds and zips the labelled dataset
on request. Saved runs can be listed and reproduced. The runner needs Docker on
the host; the GUI never shells out — it calls these endpoints.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import zipfile
from pathlib import Path

import yaml

from .. import scenario as sc
from ..deploy import compose as dc
from ..protocols import wdsproto as wp
from ..attacks import catalog, dataset
from .live import ROOT

ATTACK_RUNS = ROOT / "runs" / "attacks"
DATASETS = ROOT / "runs" / "datasets"

# plant-snapshot column suffix per WDS data type (see wds/engine.snapshot)
_GT_SUFFIX = {wp.DT_LEVEL: "level", wp.DT_FLOW: "flow", wp.DT_PRESSURE: "pressure",
              wp.DT_DEMAND: "demand", wp.DT_SETTING: "setting", wp.DT_STATUS: "status",
              wp.DT_HEAD: "head", wp.DT_VOLUME: "volume", wp.DT_SPEED: "speed"}


def _victim_view(scen: sc.Scenario, attacks: list[dict]) -> dict | None:
    """Pick a sensor attack to visualise and map it to (gt_col, mtu_col)."""
    for a in attacks:
        if a.get("kind") in ("fdi", "replay") and a.get("tag"):
            try:
                tag = scen.plcs[a["target"]].tag(a["tag"])
            except Exception:
                continue
            gt = f"{tag.element}_{_GT_SUFFIX.get(tag.dtype, '')}"
            return {"plc": a["target"], "tag": a["tag"],
                    "gt_col": gt, "mtu_col": f"{a['target']}.{a['tag']}",
                    "attacks": [a for a in attacks]}
    return None


class AttackJobManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.proc: asyncio.subprocess.Process | None = None
        self.name: str | None = None
        self.build: Path | None = None
        self.out: Path | None = None
        self.state = "idle"          # idle|running|done|error
        self.error: str | None = None
        self.experiment: dict | None = None
        self.view: dict | None = None
        self.latest: dict | None = None
        self.summary: dict | None = None
        self._tasks: list[asyncio.Task] = []
        self._clients: set = set()

    # --------------------------------------------------------------- launch
    async def run(self, experiment: dict) -> dict:
        async with self._lock:
            if self.proc is not None and self.proc.returncode is None:
                raise RuntimeError("an attack run is already active")
            name = experiment.get("name") or "gui_run"
            scen = sc.load(experiment.get("deployment", {}).get("scenario")
                           or sc.DEFAULT_SCENARIO)
            plan = dc.plan(scen)
            attacks = experiment.get("attacks", [])
            if not attacks:
                raise RuntimeError("no attacks in the experiment")
            for i, s in enumerate(attacks):      # validate before starting Docker
                catalog.resolve(catalog.Attack.from_spec(s, i), scen, plan)

            self.name = name
            run_dir = ATTACK_RUNS / name
            self.build = run_dir / "build"
            self.out = run_dir / "out"
            run_dir.mkdir(parents=True, exist_ok=True)
            # a prior run's logs are owned by root (written by containers through
            # the bind mount); clear them with a throwaway root container so a
            # re-run / reproduce does not hit a PermissionError.
            if self.build.exists() or self.out.exists():
                await self._clean_prior(run_dir)
            expfile = run_dir / "experiment.yaml"
            expfile.write_text(yaml.dump(experiment, sort_keys=False))
            self.experiment = experiment
            self.view = _victim_view(scen, attacks)
            self.latest, self.summary, self.error = None, None, None
            self.state = "running"

            cmd = [sys.executable, "-m", "scadavt.attacks.runner", str(expfile),
                   "--build", str(self.build), "--out", str(self.out)]
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(ROOT),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            self._tasks = [asyncio.create_task(self._tail()),
                           asyncio.create_task(self._watch())]
            return self.status()

    async def _clean_prior(self, run_dir: Path):
        try:
            p = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm", "--entrypoint", "sh",
                "-v", f"{run_dir.resolve()}:/w", "scadavt-net:2.0",
                "-c", "rm -rf /w/build /w/out",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(p.wait(), 60)
        except Exception:
            pass  # fall back to the runner's own (possibly partial) cleanup

    async def stop(self) -> dict:
        async with self._lock:
            for t in self._tasks:
                t.cancel()
            self._tasks = []
            if self.proc is not None and self.proc.returncode is None:
                try:
                    self.proc.terminate()
                    await asyncio.wait_for(self.proc.wait(), 10)
                except (ProcessLookupError, asyncio.TimeoutError):
                    pass
            # best-effort teardown of any containers the runner left up
            try:
                cf = self.build / "docker-compose.yml"
                if cf.exists():
                    p = await asyncio.create_subprocess_exec(
                        "docker", "compose", "-f", str(cf), "down", "-t", "2",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(p.wait(), 30)
            except Exception:
                pass
            if self.state == "running":
                self.state = "idle"
            return self.status()

    def _hours(self) -> float:
        return float((self.experiment or {}).get("deployment", {}).get("hours", 0) or 0)

    def _progress(self) -> float:
        sim = (self.latest or {}).get("sim_h") or 0.0
        h = self._hours()
        return round(min(1.0, sim / h), 4) if h else 0.0

    def _phase(self) -> str:
        if self.state == "running":
            return "starting containers" if self.latest is None else "running"
        return self.state

    def status(self) -> dict:
        return {"state": self.state, "phase": self._phase(), "name": self.name,
                "error": self.error, "progress": self._progress(), "hours": self._hours(),
                "view": {k: self.view[k] for k in ("plc", "tag", "gt_col", "mtu_col")}
                if self.view else None,
                "latest": self.latest, "metrics": (self.summary or {}).get("metrics"),
                "out": str(self.out) if self.out else None}

    # --------------------------------------------------------------- stream
    def register(self, ws):
        self._clients.add(ws)

    def unregister(self, ws):
        self._clients.discard(ws)

    async def _broadcast(self, msg: dict):
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                self._clients.discard(ws)

    def _active(self, sim_h: float) -> list[str]:
        return [a.get("id", a["kind"]) for a in (self.experiment or {}).get("attacks", [])
                if a["start_h"] <= sim_h < a["end_h"]]

    async def _tail(self):
        gt = self.build / "logs" / "ground_truth.csv"
        hist = self.build / "logs" / "historian.csv"
        gpos = hpos = 0
        ghead = hhead = None
        gt_col = (self.view or {}).get("gt_col")
        mtu_col = (self.view or {}).get("mtu_col")
        last_true = last_seen = None
        last_sim = 0.0
        while True:
            # ground truth -> true value + sim clock
            if gt.exists():
                with open(gt) as fh:
                    fh.seek(gpos)
                    for line in fh:
                        if not line.strip():
                            continue
                        cells = line.rstrip("\n").split(",")
                        if ghead is None:
                            ghead = cells
                            continue
                        row = dict(zip(ghead, cells))
                        try:
                            last_sim = float(row.get("sim_h", last_sim))
                        except ValueError:
                            pass
                        if gt_col and gt_col in row:
                            try:
                                last_true = float(row[gt_col])
                            except ValueError:
                                pass
                    gpos = fh.tell()
            # historian -> MTU-observed value
            if hist.exists():
                with open(hist) as fh:
                    fh.seek(hpos)
                    for line in fh:
                        if not line.strip():
                            continue
                        cells = line.rstrip("\n").split(",")
                        if hhead is None:
                            hhead = cells
                            continue
                        row = dict(zip(hhead, cells))
                        if mtu_col and mtu_col in row:
                            try:
                                last_seen = float(row[mtu_col])
                            except ValueError:
                                pass
                    hpos = fh.tell()
            active = self._active(last_sim)
            self.latest = {"sim_h": round(last_sim, 3), "true": last_true,
                           "mtu_seen": last_seen, "attack_active": bool(active),
                           "active_ids": active}
            await self._broadcast({"type": "attack_frame", "progress": self._progress(),
                                   "phase": self._phase(), **self.latest})
            await asyncio.sleep(0.4)

    async def _watch(self):
        assert self.proc is not None
        rc = await self.proc.wait()
        for t in self._tasks:
            if t is not asyncio.current_task():
                t.cancel()
        sfile = self.out / "summary.json" if self.out else None
        if sfile and sfile.exists():
            try:
                self.summary = json.loads(sfile.read_text())
            except Exception:
                self.summary = None
        if rc == 0 and self.summary is not None:
            self.state = "done"
        else:
            self.state = "error"
            if self.proc.stderr is not None:
                try:
                    err = await self.proc.stderr.read()
                    self.error = err.decode(errors="replace")[-600:] or f"exit {rc}"
                except Exception:
                    self.error = f"exit {rc}"
        await self._broadcast({"type": "attack_status", **self.status()})

    # -------------------------------------------------------------- dataset
    def build_dataset(self) -> dict:
        if not (self.out and (self.out / "summary.json").exists()):
            raise RuntimeError("no finished run to build a dataset from")
        DATASETS.mkdir(parents=True, exist_ok=True)
        ds_dir = DATASETS / self.name
        manifest = dataset.build(self.out, ds_dir)
        zip_path = DATASETS / f"{self.name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(ds_dir.glob("*")):
                if f.is_file():
                    z.write(f, f.name)
        return {"name": self.name, "dir": str(ds_dir), "zip": str(zip_path),
                "rows": manifest["rows"], "labels": manifest["label_distribution"]}

    # --------------------------------------------------------------- results
    @staticmethod
    def list_results() -> list[dict]:
        out = []
        if ATTACK_RUNS.exists():
            for d in sorted(ATTACK_RUNS.iterdir()):
                exp = d / "experiment.yaml"
                summ = d / "out" / "summary.json"
                if exp.exists():
                    doc = yaml.safe_load(exp.read_text()) or {}
                    out.append({"name": d.name,
                                "attacks": [(a.get("kind"), a.get("target"))
                                            for a in doc.get("attacks", [])],
                                "finished": summ.exists()})
        return out


manager = AttackJobManager()
