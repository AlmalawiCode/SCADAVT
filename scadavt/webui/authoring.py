"""Authoring backend: safe, validity-checked editing of a scenario, plus attack
composition for the GUI.

Editing never touches the packaged scenario: the first edit copies the scenario
into a writable workspace under ``runs/workspace/``. Every structural save is
followed by a real EPANET solve; if the edited model does not solve, the change
is reverted. Attacks composed in the GUI are validated with the same catalogue
gate used by the runner and exported as a ready-to-run experiment file.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from .. import scenario as sc
from ..deploy import compose as dc
from ..wds.model import Plant
from ..attacks import catalog
from .live import ROOT

WORKSPACE = ROOT / "runs" / "workspace"


# ----------------------------------------------------------------- workspace
def ensure_workspace(name: str = "town") -> Path:
    """Return a writable scada.yaml, copying the packaged scenario on first use."""
    dst = WORKSPACE / name
    if not (dst / "scada.yaml").exists():
        src = Path(sc.DEFAULT_SCENARIO).parent
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.suffix in (".yaml", ".inp"):
                shutil.copy(f, dst / f.name)
    return dst / "scada.yaml"


# ------------------------------------------------------------- validity check
def check_validity(scenario_path: str | Path) -> dict:
    """Load the scenario and run a short EPANET solve; report ok / error."""
    try:
        scen = sc.load(scenario_path)
        plant = Plant(scen, seed=1)
        try:
            plant.advance(300)
            plant.advance(300)
        finally:
            plant.close()
        return {"ok": True, "error": None}
    except Exception as e:  # EPANET / model error
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# -------------------------------------------------------------- layout saving
def save_coordinates(scenario_path: str | Path, coords: dict[str, list[float]]) -> dict:
    """Write node coordinates into the scenario's ``.inp`` and validity-check it.

    ``coords`` maps node id -> ``[x, y]`` in EPANET space. The model is solved
    after the edit; if it no longer solves, the edit is reverted.
    """
    scen = sc.load(scenario_path)
    inp = Path(scen.epanet_file)
    original = inp.read_text()
    out, section = [], None
    for line in original.splitlines():
        s = line.split(";", 1)[0].strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().upper()
            out.append(line)
            continue
        if section == "COORDINATES" and s:
            nid = s.split()[0]
            if nid in coords:
                x, y = coords[nid]
                out.append(f" {nid:<16}{float(x):>12.1f}{float(y):>12.1f}")
                continue
        out.append(line)
    inp.write_text("\n".join(out) + ("\n" if original.endswith("\n") else ""))
    chk = check_validity(scenario_path)
    if not chk["ok"]:
        inp.write_text(original)  # revert an edit that breaks the hydraulics
        chk["reverted"] = True
    else:
        chk["saved"] = len(coords)
    return chk


# ----------------------------------------------------------------- attacks
def validate_attacks(scenario_path: str | Path, specs: list[dict]) -> list[dict]:
    """Validate attack specs against the scenario (the catalogue gate)."""
    scen = sc.load(scenario_path)
    plan = dc.plan(scen)
    out = []
    for i, spec in enumerate(specs):
        sid = str(spec.get("id") or f"a{i + 1}")
        try:
            atk = catalog.Attack.from_spec(spec, i)
            r = catalog.resolve(atk, scen, plan)
            out.append({"id": sid, "ok": True,
                        "resolved": {k: r[k] for k in ("field", "victim_ip", "address")
                                     if k in r}})
        except Exception as e:
            out.append({"id": sid, "ok": False, "error": str(e)})
    return out


DEFAULT_DEPLOYMENT = {"profile": "full", "seed": 1, "start": "2025-01-23",
                      "hours": 24, "step": 300, "tick": 1.0, "period": "1.0",
                      "capture": True}


def build_experiment_yaml(name: str, attacks: list[dict],
                          deployment: dict | None = None) -> str:
    doc = {"name": name, "deployment": deployment or dict(DEFAULT_DEPLOYMENT),
           "attacks": attacks}
    return yaml.dump(doc, sort_keys=False, width=100)
