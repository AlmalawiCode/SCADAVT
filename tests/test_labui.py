"""Tests for the lab UI/API: config builder, preview simulation (incl. attacks),
and the HTTP API that both the web builder and the terminal client use."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scadavt.core import coregen
from scadavt.labui.app import app
from scadavt.labui.config import compose_lab, make_lab
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


def _drawn_design():
    return {"name": "drawn", "hours": 48,
            "nodes": [{"id": "R1", "kind": "reservoir", "head": 50},
                      {"id": "J1", "kind": "junction", "demand": 11, "elev": 0},
                      {"id": "T1", "kind": "tank", "init": 3.5, "min": 0.1, "max": 6, "diam": 6},
                      {"id": "T2", "kind": "tank", "init": 4.0, "min": 0.1, "max": 6, "diam": 6}],
            "links": [{"id": "P1", "kind": "pump", "n1": "R1", "n2": "J1", "power": 11},
                      {"id": "PI1", "kind": "pipe", "n1": "J1", "n2": "T1", "diam": 300},
                      {"id": "V1", "kind": "valve", "n1": "J1", "n2": "T2",
                       "vtype": "TCV", "setting": 5, "diam": 300}]}


def test_compose_derives_bindings_and_rules():
    plcs = [{"id": "PLC1", "elements": ["T1", "P1"]},
            {"id": "PLC2", "elements": ["T2", "V1"]}]
    cfg = compose_lab(_drawn_design(), plcs)
    assert coregen.validate(cfg) == []
    regs = {d["id"]: [p["id"] for p in d["points"]] for d in cfg["devices"]}
    assert regs["PLC1"] == ["T1_LEV", "P1_CMD", "P1_FLOW"]
    assert regs["PLC2"] == ["T2_LEV", "V1_SET", "V1_FLOW"]
    assert [r["id"] for r in cfg["rules"]] == ["on_P1", "off_P1"]   # auto: tank+pump only


def test_import_inp_parses_and_draws_and_runs_authoritatively():
    # pretend this .inp was built and exported from EPANET
    from scadavt.builder import generate
    from scadavt.labui.config import compose_lab
    from scadavt.labui.inp import parse_inp
    inp_text = generate.generate_inp({"name": "epa", "nodes": [
        {"id": "R1", "kind": "reservoir", "x": 0, "y": 0, "head": 50},
        {"id": "J1", "kind": "junction", "x": 40, "y": 0, "elev": 0, "demand": 11},
        {"id": "T1", "kind": "tank", "x": 80, "y": 0, "elev": 20, "init": 3.5,
         "min": 0.1, "max": 6, "diam": 6}],
        "links": [{"id": "P1", "kind": "pump", "n1": "R1", "n2": "J1", "power": 11},
                  {"id": "PI1", "kind": "pipe", "n1": "J1", "n2": "T1", "diam": 300}]})
    # upload -> parse (drawn elements + coordinates kept, raw text kept)
    r = client.post("/import_inp", json={"inp": inp_text}).json()
    d = r["design"]
    assert r["summary"]["by_kind"] == {"reservoir": 1, "junction": 1, "tank": 1,
                                       "pipe": 1, "pump": 1}
    assert all("x" in n and "y" in n for n in d["nodes"])        # coordinates for drawing
    assert d["inp_text"] == inp_text                              # authoritative text kept
    # assign a PLC and run ON THE UPLOADED FILE
    cfg = compose_lab(d, [{"id": "PLC1", "elements": ["T1", "P1"]}])
    assert cfg["epanet"]["inp_text"] == inp_text                  # run uses the uploaded .inp
    rr = client.post("/run", json={"config": cfg, "cycles": 30}).json()
    assert rr["engine"] == "epanet" and "PLC1" in rr["traces"]


def test_design_app_serves_any_design_over_integration_api():
    """The generic external app exposes a config's EPANET design over the public
    API (the external program a CORE/networked run connects to)."""
    import asyncio
    from scadavt.core import integration as ig
    from scadavt.examples.design_app import DesignApp

    cfg = make_lab(1)

    async def main():
        app = DesignApp(cfg["epanet"], cfg["mapping"])
        server = await app.serve()
        c = ig.IntegrationClient("127.0.0.1", server.sockets[0].getsockname()[1])
        try:
            await c.hello()
            pts = {p.id for p in await c.list_points()}
            assert "T1_LEV" in pts and "P1_CMD" in pts
            r = await c.acquire("T1_LEV")
            assert r.quality == ig.OK and 0.0 <= r.value <= 6.0   # real EPANET level
            assert await c.control("P1_CMD", 1.0)                  # command accepted
        finally:
            await c.close()
            server.close()
            app.close()
    asyncio.run(main())


def test_compose_with_per_plc_rules_text_and_run_frames():
    """Per-PLC instructions in the natural RULES grammar are parsed, and /run
    returns per-cycle frames (tank levels + pump states) for the live diagram."""
    cfg = make_lab(1)
    rt = ("IF TANK T1 LEVEL BELOW 2.5 THEN PUMP P1 STATUS IS OPEN\n"
          "IF TANK T1 LEVEL ABOVE 5.0 THEN PUMP P1 STATUS IS CLOSED")
    comp = client.post("/compose", json={"design": cfg["epanet"],
                                         "plcs": cfg["epanet"]["plcs"],
                                         "rules_text": rt}).json()
    assert comp["issues"] == [] and len(comp["config"]["rules"]) == 2
    r = client.post("/run", json={"config": comp["config"], "cycles": 20}).json()
    assert len(r["frames"]) == 20
    f0 = r["frames"][0]
    assert "T1" in f0["tanks"] and "P1" in f0["pumps"]
    # the tank level moves over the run (rise/fall), driven by EPANET + the rule
    levels = [f["tanks"]["T1"] for f in r["frames"]]
    assert min(levels) < max(levels)


def test_api_compose_then_run_on_epanet():
    plcs = [{"id": "PLC1", "elements": ["T1", "P1"]}]
    comp = client.post("/compose", json={"design": _drawn_design(), "plcs": plcs}).json()
    assert comp["issues"] == []
    r = client.post("/run", json={"config": comp["config"], "cycles": 40}).json()
    assert r["engine"] == "epanet" and "PLC1" in r["traces"]
    lv = r["traces"]["PLC1"]
    assert all(0.0 <= v <= 6.0 for v in lv)


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


def test_networked_run_over_sockets():
    """The full networked stack (device nodes + gateway + EPANET app + MTU over
    Modbus) runs end to end over real sockets and drives the tank."""
    import asyncio
    from scadavt.labui.netrun import NetworkedRun

    async def main():
        res = await NetworkedRun(make_lab(1)).run(25)
        assert res["engine"] == "networked" and "PLC1" in res["traces"]
        lv = res["traces"]["PLC1"]
        assert all(0.0 <= v <= 6.0 for v in lv) and min(lv) < max(lv)
        # with a DoS window the schedule is recorded
        nr = NetworkedRun(make_lab(1))
        nr.add_attack("dos", "PLC1", 8, 20)
        s = (await nr.run(25))["summary"]
        assert s["attack_window_cycles"] == 12
    asyncio.run(main())


def test_api_netrun_endpoint():
    cfg = make_lab(1)
    r = client.post("/netrun", json={"config": cfg, "cycles": 20}).json()
    assert r["engine"] == "networked" and "PLC1" in r["traces"]


def test_thesis_example_config_runs(tmp_path):
    """The shipped thesis example (water distribution) validates and runs on the
    real EPANET engine with its 6 tanks."""
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scadavt" / "scenarios" / "thesis_example"
    if not (p / "watertown.json").exists():
        import pytest
        pytest.skip("thesis example not generated")
    cfg = json.loads((p / "watertown.json").read_text())
    assert coregen.validate(cfg) == []
    assert len(cfg["epanet"]["plcs"]) == 5 and len(cfg["devices"]) == 5
    r = client.post("/run", json={"config": cfg, "cycles": 60}).json()
    assert r["engine"] == "epanet"
    assert set(r["frames"][0]["tanks"]) >= {"T1", "T2", "T3", "T4", "T5", "T6"}


def test_core_launch_orchestrator(tmp_path):
    """The launcher prepares the CORE package, runs the pipeline, and exports a
    reproducible bundle (pcaps would be added on a CORE host)."""
    import asyncio
    from pathlib import Path
    from scadavt.labui.launch import CoreLauncher

    lab = CoreLauncher(make_lab(1), tmp_path)
    assert isinstance(lab.core_available(), bool)
    assert len(lab.plan()) >= 6
    res = asyncio.run(lab.run(cycles=25, attacks=[{"kind": "integrity", "target": "PLC1",
                                                   "start": 5, "end": 15}]))
    # CORE bring-up package written
    for f in ("core_session.py", "run_devices.sh", "lab.json", "launch.sh",
              "gate_endpoints.ini"):
        assert (tmp_path / f).exists()
    assert Path(res["launch_script"]).exists()
    # experiment bundle written with both label dimensions
    b = Path(res["bundle"])
    assert (b / "trace.csv").exists() and (b / "labels.csv").exists()
    assert (b / "metadata.json").exists()
    assert res["engine"] == "networked"
    assert res["summary"]["confirmed_malicious_cycles"] >= 0


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
