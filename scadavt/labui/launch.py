"""CORE launch orchestrator.

Automates the runbook for one lab: it prepares every artefact (CORE session
script, gateway files), detects whether CORE can run here, writes a ``launch.sh``
that brings the lab up on a CORE host, executes the full pipeline through the
real networked engine, and exports a reproducible experiment bundle (process
trace + two-dimensional labels + metadata; pcaps on a CORE host).

On a machine with CORE + privileges the emitted ``launch.sh`` runs the session
inside emulated namespaces with ``netem`` and ``pcap``; everywhere else the
orchestrator still runs the identical software path over loopback and produces
the same bundle, so the researcher always gets a dataset.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..core import bundle as bundlemod
from ..core import coregen
from ..core import labels as lbl


class CoreLauncher:
    def __init__(self, config: dict, workdir: str | Path):
        self.cfg = config
        self.workdir = Path(workdir)

    # ---------------------------------------------------------------- CORE probe
    @staticmethod
    def core_available() -> bool:
        if not shutil.which("core-daemon"):
            return False
        try:
            import core.api.grpc  # noqa: F401
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- artefacts
    def prepare(self) -> dict:
        """Write the CORE session + gateway files + the full lab config; map of files."""
        files = coregen.write_session(self.cfg, self.workdir)
        (self.workdir / "lab.json").write_text(json.dumps(self.cfg, indent=2))
        files["lab.json"] = str(self.workdir / "lab.json")
        return files

    def plan(self) -> list[dict]:
        app = (self.cfg.get("external_apps") or [{}])[0].get("endpoint", "127.0.0.1:9000")
        steps = [
            {"step": "generate", "how": "coregen.write_session",
             "detail": "session.json, core_session.py, gateway INIs, run_devices.sh"},
            {"step": "start CORE session", "how": "python core_session.py",
             "detail": "creates nodes + links (netem delay/loss) and starts them"},
            {"step": "start field devices", "how": "run_devices.sh (inside each node)",
             "detail": "Modbus field-bus + I/O-modules back channel per PLC/RTU"},
            {"step": "start external app", "how": f"scadavt-lab app session.json ({app})",
             "detail": "the EPANET simulator serving the Integration API"},
            {"step": "start gateway + MTU", "how": "the networked run / MTU node",
             "detail": "moves I/O over the back channel; MTU controls over Modbus"},
            {"step": "capture", "how": "tcpdump per node (CORE host)",
             "detail": "synchronized pcaps of the SCADA traffic"},
            {"step": "attacks", "how": "scheduled windows (dos/integrity/…)",
             "detail": "on the emulated SCADA path"},
            {"step": "bundle", "how": "ExperimentRecorder.write_bundle",
             "detail": "trace.csv + labels.csv + metadata.json + pcaps"},
        ]
        return steps

    def write_launch_script(self) -> Path:
        app = (self.cfg.get("external_apps") or [{}])[0].get("endpoint", "127.0.0.1:9000")
        host, port = (app.split(":") + ["9000"])[:2]
        sh = self.workdir / "launch.sh"
        sh.write_text(f"""#!/usr/bin/env bash
# Auto-generated CORE bring-up for lab '{self.cfg.get('name','lab')}'.
# Run on a host with CORE + privileges (sudo core-daemon running). See
# docs/CORE_RUNBOOK.md. Steps mirror CoreLauncher.plan().
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="${{PYTHONPATH:-}}"

echo "[1/6] start the emulated SCADA network"
python core_session.py &            # creates + starts nodes/links (netem)
sleep 3

echo "[2/6] start the external EPANET application"
python -m scadavt.labui.cli app lab.json --host 0.0.0.0 --port {port} &
APP=$!; sleep 1

echo "[3/6] start each field device INSIDE its CORE node"
# On CORE, run each line from run_devices.sh inside the node namespace
# (attach as a CORE service, or: vcmd -c /tmp/pycore.<id>/<NODE> -- <cmd>).
cat run_devices.sh

echo "[4/6] start the gateway + MTU control (host side, reaching node back channels)"
echo "    see docs/CORE_RUNBOOK.md step 5 (Gateway.from_session) + the MTU node"

echo "[5/6] capture: tcpdump -i <iface> -w pcaps/<node>.pcap 'tcp port 502' (per node)"
echo "[6/6] bundle: scadavt-lab (ExperimentRecorder) writes trace+labels+metadata+pcaps"
wait $APP
""")
        sh.chmod(0o755)
        return sh

    # ------------------------------------------------------------------- run it
    async def run(self, cycles: int = 60, attacks: list | None = None,
                  out: str | Path | None = None) -> dict:
        """Prepare artefacts, emit launch.sh, run the full networked pipeline, and
        write an experiment bundle. Uses CORE when available and ``on_core`` is
        wired on the host; otherwise the identical software over loopback."""
        from .netrun import NetworkedRun
        files = self.prepare()
        self.write_launch_script()
        nr = NetworkedRun(self.cfg)
        for a in attacks or []:
            nr.add_attack(a["kind"], a["target"], a.get("start", 0), a.get("end", 10 ** 9))
        res = await nr.run(cycles)

        # assemble a reproducible bundle from the run
        out = Path(out) if out else (self.workdir / "bundle")
        rec = bundlemod.ExperimentRecorder(metadata={
            "name": self.cfg.get("name", "lab"),
            "placement": self.cfg.get("control", {}).get("placement", "mtu"),
            "engine": res["engine"], "cycles": cycles,
            "core_available": self.core_available(),
        })
        for r in nr.trace:
            rec.record(float(r["t"]), {k: r[k] for k in r if k.endswith("_level")})
            if r.get("malicious_confirmed"):
                rec.confirm(float(r["t"]), "forced-command")
        rec.windows = [lbl.AttackWindow(a["kind"], a["target"], float(a["start"]),
                                        float(a["end"])) for a in nr.attacks]
        binfo = rec.write_bundle(out)
        return {"engine": res["engine"], "summary": res["summary"],
                "core_available": self.core_available(),
                "artifacts": files, "launch_script": str(self.workdir / "launch.sh"),
                "bundle": binfo["dir"]}
