"""Run one experiment on the networked deployment (run on the Docker host).

    python -m scadavt.experiments.networked experiments/e3_plc6_link.yaml

The experiment file names the deployment settings and a list of fault windows
in simulated hours. The runner

1. generates the deployment (``scadavt.deploy.compose``) and starts it;
2. follows the simulated clock of the WaterSystem server (SIMCLOCK request
   on the back channel, reachable from the Docker host);
3. opens and closes every fault window at its simulated start and end time,
   and records the wall-clock and simulated times of each action (labels);
4. waits until the plant has run the requested hours, stops the deployment,
   and computes the measures of the paper from the ground-truth log of the
   plant, so that the result is directly comparable with the process-level
   runs of ``scadavt.experiments.suite``.

Built-in fault kinds act on the network only, never on the SCADA programs:

    link_loss    100 % packet loss on the field-LAN interface of a node
                 (the control channel to that node is lost)
    link_rate    rate limit on the field-LAN interface of a node (``value``)
    router_stop  freeze a router container (a whole site loses its link)
    hook         run ``start_cmd`` at the start and ``end_cmd`` at the end
                 (shell commands supplied by the experimenter)

Example file::

    name: e3_plc6_link
    deployment: {profile: full, seed: 1, start: 2025-01-23, hours: 168,
                 step: 300, tick: 1.0, period: "1.0"}
    faults:
      - {start_h: 15, end_h: 20, kind: link_loss, target: plc6}
      - {start_h: 55, end_h: 57, kind: link_loss, target: plc6}
      - {start_h: 100, end_h: 105, kind: link_loss, target: plc6}
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import subprocess
import time
from pathlib import Path

import pandas as pd
import yaml

from ..protocols import wdsproto as wp
from .suite import metrics

ROOT = Path(__file__).resolve().parents[2]
LAN_IF = "$(ip -o route get $GATEWAY | sed -n 's/.* dev \\([^ ]*\\).*/\\1/p')"


def sh(cmd, check=True):
    return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT).stdout


class Runner:
    def __init__(self, exp: dict, build: Path, out: Path):
        self.exp, self.build, self.out = exp, build, out
        self.compose = ["docker", "compose", "-f", str(build / "docker-compose.yml")]
        self.actions: list[dict] = []

    # --------------------------------------------------------- deployment
    def generate(self):
        d = self.exp.get("deployment", {})
        args = ["python", "-m", "scadavt.deploy.compose", "--out", str(self.build),
                "--profile", d.get("profile", "full"), "--project", d.get("project", "scadavt")]
        for k in ("seed", "start", "hours", "step", "tick", "period", "access_rate",
                  "wan_delay", "image"):
            if k in d:
                args += [f"--{k.replace('_', '-')}", str(d[k])]
        if d.get("capture"):
            args.append("--capture")
        sh(args)
        for f in (self.build / "logs").rglob("*"):
            if f.is_file():
                f.unlink()

    def up(self):
        sh(self.compose + ["up", "-d"])

    def down(self):
        sh(self.compose + ["down", "-t", "2"], check=False)

    # -------------------------------------------------------------- faults
    def apply(self, f: dict, phase: str, sim_h: float):
        kind, target = f["kind"], f.get("target", "")
        if kind == "link_loss":
            cmd = (f"tc qdisc replace dev {LAN_IF} root netem loss 100%" if phase == "start"
                   else f"tc qdisc del dev {LAN_IF} root")
            res = sh(self.compose + ["exec", "-T", target, "sh", "-c", cmd], check=False)
        elif kind == "link_rate":
            cmd = (f"/opt/scadavt-net/shape.sh {LAN_IF} {f['value']}" if phase == "start"
                   else f"tc qdisc del dev {LAN_IF} root")
            res = sh(self.compose + ["exec", "-T", target, "sh", "-c", cmd], check=False)
        elif kind == "router_stop":
            # pause/unpause freezes the router at once and keeps its routes
            res = sh(self.compose + ["pause" if phase == "start" else "unpause", target],
                     check=False)
        elif kind == "hook":
            res = sh(["sh", "-c", f["start_cmd" if phase == "start" else "end_cmd"]], check=False)
        else:
            raise ValueError(f"unknown fault kind {kind}")
        self.actions.append({"wall_time": round(time.time(), 3), "sim_h": round(sim_h, 4),
                             "phase": phase, "kind": kind, "target": target,
                             "output": res.strip()[:200]})
        print(f"[{sim_h:7.2f} h] {phase:5s} {kind} {target}")

    # --------------------------------------------------------------- clock
    async def sim_hours(self, host: str, port: int) -> float | None:
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 2.0)
            w.write(wp.WDSMessage("SIMCLOCK", wp.FC_ACQUIRE, 0, 0).pack())
            await w.drain()
            raw = await asyncio.wait_for(r.readexactly(wp.PDU_SIZE), 2.0)
            w.close()
            return wp.WDSMessage.unpack(raw).data
        except Exception:
            return None

    async def follow(self, host="172.16.0.20", port=9000, poll=0.2):
        hours = float(self.exp.get("deployment", {}).get("hours", 168))
        faults = sorted(self.exp.get("faults", []), key=lambda f: f["start_h"])
        events = [(f["start_h"], "start", f) for f in faults] + \
                 [(f["end_h"], "end", f) for f in faults]
        events.sort(key=lambda e: (e[0], e[1] == "start"))
        last, misses = 0.0, 0
        while True:
            h = await self.sim_hours(host, port)
            if h is None:
                misses += 1
                if misses > 50 and last > 0:
                    break  # the server has finished and closed
                await asyncio.sleep(poll)
                continue
            misses, last = 0, h
            while events and events[0][0] <= h:
                t, phase, f = events.pop(0)
                self.apply(f, phase, h)
            if h >= hours - 1e-6:
                break
            await asyncio.sleep(poll)
        for t, phase, f in events:  # close any window still open
            if phase == "end":
                self.apply(f, phase, last)

    # ------------------------------------------------------------ results
    def collect(self):
        self.out.mkdir(parents=True, exist_ok=True)
        logs = self.build / "logs"
        for f in logs.glob("*.csv"):
            (self.out / f.name).write_bytes(f.read_bytes())
        pc = logs / "pcap"
        if pc.exists():
            (self.out / "pcap").mkdir(exist_ok=True)
            for f in pc.glob("*.pcap"):
                (self.out / "pcap" / f.name).write_bytes(f.read_bytes())
        with open(self.out / "faults.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["wall_time", "sim_h", "phase", "kind", "target",
                                               "output"])
            w.writeheader()
            w.writerows(self.actions)
        gt = self.out / "ground_truth.csv"
        summary = {"name": self.exp.get("name"), "deployment": self.exp.get("deployment"),
                   "faults": self.exp.get("faults", [])}
        if gt.exists():
            df = pd.read_csv(gt)
            summary["metrics"] = metrics(df)
            summary["rows"] = len(df)
            summary["sim_hours"] = float(df["sim_h"].iloc[-1])
            summary["wall_seconds"] = float(df["wall_time"].iloc[-1] - df["wall_time"].iloc[0])
        (self.out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
        return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiment", type=Path)
    ap.add_argument("--build", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep", action="store_true", help="leave the deployment running")
    a = ap.parse_args(argv)
    exp = yaml.safe_load(a.experiment.read_text())
    name = exp.get("name", a.experiment.stem)
    r = Runner(exp, a.build or ROOT / "build" / name, a.out or ROOT / "results" / "networked" / name)
    r.down()
    r.generate()
    t0 = time.time()
    r.up()
    try:
        asyncio.run(r.follow())
    finally:
        time.sleep(3)
        if not a.keep:
            r.down()
    s = r.collect()
    s["runner_wall_seconds"] = round(time.time() - t0, 1)
    print(json.dumps({k: s.get(k) for k in ("name", "sim_hours", "wall_seconds", "metrics")},
                     indent=1, default=str)[:1500])


if __name__ == "__main__":
    main()
