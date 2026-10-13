"""Reproducible check of the emulated SCADA network (run on the Docker host).

    python -m scadavt.deploy.netcheck --repeats 5 --out results/network

For each repetition the script generates the validation deployment, starts
it and measures:

* the time to start all containers and the memory they use;
* TCP connection time and round-trip time on every MTU-to-PLC path (routed
  through the control router, the WAN and a field router) and on every
  back-channel path from the gateway;
* isolation: with one field router stopped, only the PLCs of that site must
  become unreachable;
* shaping (last repetition only): with a WAN delay and a rate-limited access
  link, the round-trip time and the throughput of one access link.

Results are written as CSV and JSON files in ``--out``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sh(cmd: list[str], check=True, capture=True) -> str:
    r = subprocess.run(cmd, check=check, text=True,
                       stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT)
    return r.stdout or ""


class Deployment:
    def __init__(self, build: Path, extra: list[str]):
        self.build = build
        self.extra = extra
        self.compose = ["docker", "compose", "-f", str(build / "docker-compose.yml")]

    def generate(self):
        sh(["python", "-m", "scadavt.deploy.compose", "--out", str(self.build), "--profile",
            "validate", "--hours", "2", "--tick", "0.5", *self.extra])
        logs = self.build / "logs"
        for f in logs.glob("*"):
            if f.is_file():
                f.unlink()

    def up(self) -> float:
        t0 = time.perf_counter()
        sh(self.compose + ["up", "-d"])
        return time.perf_counter() - t0

    def down(self):
        sh(self.compose + ["down", "-t", "1"], check=False)

    def exec(self, svc: str, *args: str) -> str:
        return sh(self.compose + ["exec", "-T", svc, *args])

    def validate(self, side: str, out: str, n=50, timeout=5.0) -> list[dict]:
        self.exec(side, "python", "-m", "scadavt.deploy.validate", "--side", side, "--n", str(n),
                  "--timeout", str(timeout), "--out", f"/logs/{out}")
        with open(self.build / "logs" / out) as fh:
            return list(csv.DictReader(fh))


def memory_mib(project: str) -> list[tuple[str, float]]:
    out = sh(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"])
    rows = []
    for line in out.splitlines():
        name, usage = line.split()[0], line.split()[1]
        if not name.startswith(project + "-"):
            continue
        v = float(re.match(r"[\d.]+", usage).group())
        unit = re.search(r"[KMG]i?B", usage).group()
        rows.append((name, v / 1024 if unit[0] == "K" else v * 1024 if unit[0] == "G" else v))
    return rows


def summary(rows: list[dict], key: str) -> dict:
    vals = [float(r[key]) for r in rows if r["ok"] == "1"]
    return {"ok": sum(r["ok"] == "1" for r in rows), "n": len(rows),
            "median": round(statistics.median(vals), 3) if vals else None,
            "max": round(max(vals), 3) if vals else None}


def throughput(dep: Deployment, host: str, nbytes: int = 250_000) -> float:
    code = (
        "import socket,time,threading\n"
        f"s=socket.create_connection(('{host}',502),timeout=60)\n"
        f"d=b'x'*{nbytes};g=0\n"
        "def rx():\n"
        "  global g\n"
        "  while g<len(d):\n"
        "    b=s.recv(65536)\n"
        "    if not b: break\n"
        "    g+=len(b)\n"
        "t=threading.Thread(target=rx);t0=time.perf_counter();t.start();s.sendall(d);t.join()\n"
        "print(len(d)*8/(time.perf_counter()-t0)/1e6)\n")
    return float(dep.exec("mtu", "python", "-c", code).strip().splitlines()[-1])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--build", type=Path, default=ROOT / "build" / "netcheck")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "network")
    ap.add_argument("--project", default="scadavt")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    report = {"repeats": []}
    dep = Deployment(a.build, [])
    for k in range(1, a.repeats + 1):
        dep.down()
        dep.generate()
        t_up = dep.up()
        time.sleep(4)
        mem = memory_mib(a.project)
        mtu = dep.validate("mtu", f"mtu_{k}.csv")
        gw = dep.validate("gateway", f"gateway_{k}.csv")
        dep.exec("mtu", "true")
        sh(dep.compose + ["stop", "-t", "1", "router_d"])
        cut = dep.validate("mtu", f"mtu_router_d_down_{k}.csv", n=3, timeout=2.0)
        sh(dep.compose + ["start", "router_d"])
        time.sleep(2)
        back = dep.validate("mtu", f"mtu_router_d_restored_{k}.csv", n=3)
        lost = sorted(r["target"] for r in cut if r["ok"] == "0")
        rec = {"repeat": k, "deploy_s": round(t_up, 2), "containers": len(mem),
               "memory_mib_total": round(sum(m for _, m in mem), 1),
               "memory_mib_node_mean": round(statistics.mean(
                   m for n, m in mem if "router" not in n), 2),
               "memory_mib_router_mean": round(statistics.mean(
                   m for n, m in mem if "router" in n), 2),
               "mtu_paths": summary(mtu, "rtt_ms_median"),
               "mtu_connect_ms": summary(mtu, "connect_ms"),
               "gateway_paths": summary(gw, "rtt_ms_median"),
               "router_d_down_lost": lost,
               "router_d_restored_ok": sum(r["ok"] == "1" for r in back)}
        for f in (a.build / "logs").glob("*.csv"):
            f.replace(a.out / f.name)
        report["repeats"].append(rec)
        print(json.dumps(rec))
    # shaping test on a fresh deployment
    dep.down()
    shaped = Deployment(a.build, ["--access-rate", "1mbit", "--wan-delay", "10ms"])
    shaped.generate()
    shaped.up()
    time.sleep(4)
    rows = shaped.validate("mtu", "mtu_shaped.csv", n=20)
    report["shaped"] = {"access_rate": "1mbit", "wan_delay_one_way": "10ms",
                        "mtu_paths": summary(rows, "rtt_ms_median"),
                        "throughput_mbit_plc1": round(throughput(shaped, "10.10.4.11"), 3)}
    (a.build / "logs" / "mtu_shaped.csv").replace(a.out / "mtu_shaped.csv")
    shaped.down()
    print(json.dumps(report["shaped"]))
    (a.out / "netcheck.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
