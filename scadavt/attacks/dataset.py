"""Build a labelled, reproducible IDS dataset from an attack experiment result.

Joins the plant ground truth, the MTU historian and the network logs, derives a
per-row label from the logged attack windows, and writes a self-describing
bundle with a provenance manifest sufficient to reproduce the run bit-for-bit.

    python -m scadavt.attacks.dataset --result results/attacks/a1_fdi_t2 \\
        --out datasets/a1_fdi_t2

Bundle:
    process.csv   ground truth + MTU-observed values/commands, per control cycle
    network.csv   per-cycle gateway and MTU-connection features
    labels.csv    binary (normal/attack) and multiclass (attack kind) per row
    manifest.json hashes, seed, image digest, git commit, attack schedule
    README.md     how to reproduce
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

from .. import scenario as sc


def _sha256(path: Path) -> str | None:
    if not path or not Path(path).exists():
        return None
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def _git_commit(cwd: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd),
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def _image_digest(image: str) -> str | None:
    try:
        out = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return out or None
    except Exception:
        return None


def _label_row(sim_h: float, attacks: list[dict]):
    active = [a for a in attacks if a.get("start_h", 0) <= sim_h < a.get("end_h", 0)]
    kinds = sorted({a["kind"] for a in active})
    return (1 if active else 0,
            "+".join(kinds) if kinds else "normal",
            {a["id"]: int(a.get("start_h", 0) <= sim_h < a.get("end_h", 0)) for a in attacks})


def build(result_dir: str | Path, out_dir: str | Path,
          scenario_path: str | Path | None = None) -> dict:
    result = Path(result_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = json.loads((result / "summary.json").read_text())
    attacks = summary.get("attacks", [])
    dep = summary.get("deployment", {})

    gt = pd.read_csv(result / "ground_truth.csv")
    # ---- process.csv: ground truth + MTU-observed values/commands ----
    proc = gt.copy()
    hist_path = result / "historian.csv"
    if hist_path.exists():
        hist = pd.read_csv(hist_path)
        hist = hist.rename(columns={c: f"mtu_{c}" for c in hist.columns
                                    if c not in ("wall_time",)})
        proc = pd.merge_asof(proc.sort_values("wall_time"),
                             hist.sort_values("wall_time"), on="wall_time",
                             direction="nearest")
    proc.to_csv(out / "process.csv", index=False)

    # ---- labels.csv aligned to process rows by sim_h ----
    lab_rows = []
    for sim_h in proc["sim_h"]:
        b, cls, per = _label_row(float(sim_h), attacks)
        row = {"sim_h": sim_h, "label_binary": b, "label_class": cls}
        row.update({f"attack_{k}": v for k, v in per.items()})
        lab_rows.append(row)
    labels = pd.DataFrame(lab_rows)
    labels.to_csv(out / "labels.csv", index=False)

    # ---- network.csv: per-cycle gateway + connection features ----
    net = None
    gw_path = result / "gateway.csv"
    if gw_path.exists():
        net = pd.read_csv(gw_path)
    conn_path = result / "connections.csv"
    if conn_path.exists():
        conn = pd.read_csv(conn_path)
        agg = conn.groupby("cycle").apply(lambda d: pd.Series({
            "n_reads": int((d.op == "read").sum()),
            "n_writes": int((d.op == "write").sum()),
            "n_failed": int((d.ok == 0).sum()),
            "connect_ms_mean": round(float(d.connect_ms.mean()), 3),
            "request_ms_mean": round(float(d.request_ms.mean()), 3),
        }), include_groups=False).reset_index()
        net = agg if net is None else net.merge(agg, on="cycle", how="outer")
    if net is not None:
        net.sort_values(net.columns[0]).to_csv(out / "network.csv", index=False)

    # ---- provenance manifest ----
    scen_path = Path(scenario_path or sc.DEFAULT_SCENARIO)
    inp_path = sc.load(scen_path).epanet_file if scen_path.exists() else None
    image = dep.get("image", "scadavt-net:2.0")
    manifest = {
        "name": summary.get("name"),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "deployment": dep,
        "seed": dep.get("seed"),
        "provenance": {
            "scenario_file": str(scen_path),
            "scenario_sha256": _sha256(scen_path),
            "epanet_file": str(inp_path) if inp_path else None,
            "epanet_sha256": _sha256(inp_path) if inp_path else None,
            "image": image,
            "image_digest": _image_digest(image),
            "git_commit": _git_commit(Path(__file__).resolve().parents[2]),
        },
        "attacks": attacks,
        "rows": int(len(proc)),
        "sim_hours": float(proc["sim_h"].iloc[-1]) if len(proc) else 0.0,
        "label_distribution": labels["label_class"].value_counts().to_dict(),
        "files": {"process": "process.csv", "labels": "labels.csv",
                  "network": "network.csv" if net is not None else None},
        "metrics": summary.get("metrics"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    _write_readme(out, manifest)
    return manifest


def _write_readme(out: Path, manifest: dict):
    atk_lines = "\n".join(
        f"- `{a['id']}` **{a['kind']}** on `{a['plc']}"
        f"{('.' + a['tag']) if a.get('tag') else ''}`, "
        f"{a['start_h']}–{a['end_h']} h" + (f", mode={a.get('mode')} value={a.get('value')}"
                                            if a['kind'] in ('fdi', 'command') else "")
        for a in manifest["attacks"])
    dep = manifest["deployment"]
    prov = manifest["provenance"]
    (out / "README.md").write_text(f"""# SCADAVT IDS dataset — {manifest['name']}

Generated {manifest['generated_at']} from the SCADAVT research lab.

## Contents
- `process.csv` — plant ground truth and MTU-observed values/commands per control cycle
- `network.csv` — per-cycle gateway and MTU-connection features
- `labels.csv` — `label_binary` (0 normal / 1 attack) and `label_class` (attack kind), one row per `process.csv` row, aligned by `sim_h`
- `manifest.json` — full provenance

## Attacks
{atk_lines or '- (none)'}

## Label distribution
{json.dumps(manifest['label_distribution'], indent=2)}

## Reproduce
This run is deterministic given the seed and the pinned inputs below.

```bash
# from the scadavt repository at commit {prov.get('git_commit')}
docker build -t scadavt:2.0 . && docker build -t scadavt-net:2.0 -f deploy/Dockerfile.net .
python -m scadavt.attacks.runner experiments/{manifest['name']}.yaml
python -m scadavt.attacks.dataset --result results/attacks/{manifest['name']} --out datasets/{manifest['name']}
```

Pinned inputs: seed `{dep.get('seed')}`, scenario sha256 `{prov.get('scenario_sha256')}`,
EPANET sha256 `{prov.get('epanet_sha256')}`, image `{prov.get('image')}`
digest `{prov.get('image_digest')}`.
""")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scenario", type=Path)
    a = ap.parse_args(argv)
    m = build(a.result, a.out, a.scenario)
    print(json.dumps({"rows": m["rows"], "sim_hours": m["sim_hours"],
                      "labels": m["label_distribution"], "out": str(a.out)}, indent=1))


if __name__ == "__main__":
    main()
