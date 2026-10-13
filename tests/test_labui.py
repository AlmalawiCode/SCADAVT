"""Tests for the lab UI/API: config builder, preview simulation (incl. attacks),
and the HTTP API that both the web builder and the terminal client use."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scadavt.core import coregen
from scadavt.labui.app import app
from scadavt.labui.config import make_lab
from scadavt.labui.sim import Lab

client = TestClient(app)


# ------------------------------------------------------------------ config/sim
def test_make_lab_valid_for_various_sizes():
    for n in (1, 3, 5):
        for mtu in (True, False):
            cfg = make_lab(n, with_mtu=mtu)
            assert coregen.validate(cfg) == []
            assert len(cfg["devices"]) == n
            assert ("mtu" in cfg) == mtu


def test_lab_runs_and_levels_bounded():
    lab = Lab(make_lab(3)).run(80)
    tr = lab.traces()
    assert set(tr) == {"PLC1", "PLC2", "PLC3"}
    assert all(0.0 <= v <= 6.0 for series in tr.values() for v in series)
    # the hysteresis rule drove each level up from its low start at some point
    assert all(max(series) > 2.5 for series in tr.values())


def test_lab_dos_attack_freezes_and_marks_window():
    lab = Lab(make_lab(2))
    lab.add_attack("dos", "PLC1", start=20, end=60)
    lab.run(80)
    s = lab.summary()
    assert s["attack_window_cycles"] == 40
    assert s["confirmed_malicious_cycles"] == 0          # dos is window-only, not confirmed


def test_lab_integrity_attack_is_confirmed_malicious():
    lab = Lab(make_lab(2))
    lab.add_attack("integrity", "PLC2", start=10, end=40)
    lab.run(60)
    s = lab.summary()
    assert s["attack_window_cycles"] == 30
    assert s["confirmed_malicious_cycles"] == 30          # forced-off = confirmed
    assert s["benign_in_window"] == 0


# ------------------------------------------------------------------ HTTP API
def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "باني مختبر" in r.text


def test_api_validate_and_generate():
    cfg = make_lab(3)
    assert client.post("/validate", json=cfg).json()["issues"] == []
    bad = make_lab(3); bad.pop("mtu")                     # mtu placement needs an mtu node
    assert client.post("/validate", json=bad).json()["issues"]
    files = client.post("/generate", json=cfg).json()["files"]
    assert set(files) >= {"session.json", "core_session.py", "run_devices.sh"}


def test_api_run_uses_real_epanet_engine():
    cfg = make_lab(2)
    r = client.post("/run", json={"config": cfg, "cycles": 80}).json()
    assert r["engine"] == "epanet"                       # real EPANET, not the preview
    assert set(r["traces"]) == {"PLC1", "PLC2"}
    # tank levels are produced by the EPANET solver and stay within the tank range
    lv = r["traces"]["PLC1"]
    assert all(0.0 <= v <= 6.0 for v in lv) and min(lv) < max(lv)
    assert r["summary"]["attack_window_cycles"] == 0
    r2 = client.post("/run", json={"config": cfg, "cycles": 80,
                                   "attacks": [{"kind": "dos", "target": "PLC1",
                                                "start": 20, "end": 60}]}).json()
    assert r2["summary"]["attack_window_cycles"] == 40    # window is schedule-based


def test_api_live_session_inject_attack_from_terminal_path():
    cfg = make_lab(2)
    sid = client.post("/sessions", json={"config": cfg}).json()["id"]
    client.post(f"/sessions/{sid}/step", json={"n": 20})        # run clean for a bit
    # inject an attack "now" (as a terminal/script would mid-run)
    inj = client.post(f"/sessions/{sid}/attack",
                      json={"kind": "integrity", "target": "PLC1", "duration": 30}).json()
    assert inj["ok"] and inj["attacks"][0]["start"] == 20
    client.post(f"/sessions/{sid}/step", json={"n": 30})
    s = client.get(f"/sessions/{sid}").json()["summary"]
    assert s["confirmed_malicious_cycles"] > 0
    assert client.post(f"/sessions/{sid}/stop").json()["ok"]
    assert client.get(f"/sessions/{sid}").status_code == 404
