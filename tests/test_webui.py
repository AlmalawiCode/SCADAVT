"""Tests for the research-lab web backend (scadavt.webui)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from scadavt.webui import authoring, epanet_topo, topology  # noqa: E402
from scadavt.webui.app import create_app  # noqa: E402
from scadavt.webui.live import RunManager  # noqa: E402


# --------------------------------------------------------------- INP parser
def test_epanet_parser():
    import scadavt.scenario as sc
    scen = sc.load()
    g = epanet_topo.parse(scen.epanet_file)
    kinds = {n["kind"] for n in g["nodes"].values()}
    assert {"junction", "tank"} <= kinds
    assert any(n["kind"] == "tank" for n in g["nodes"].values())
    # six tanks in the town scenario, all with coordinates
    tanks = [nid for nid, n in g["nodes"].items() if n["kind"] == "tank"]
    assert len(tanks) == 6
    assert all(g["nodes"][t]["x"] is not None for t in tanks)
    # every link endpoint is a known node
    for lk in g["links"].values():
        assert lk["n1"] in g["nodes"] and lk["n2"] in g["nodes"]


# -------------------------------------------------------------- topology doc
def test_topology_build():
    t = topology.build()
    assert t["nodes"] and t["links"]
    assert len(t["plcs"]) == 33
    assert len(t["fields"]) == 14
    assert set(t["tank_max_level"]) == {f"T{i}" for i in range(1, 7)}
    # every drawable link resolves to placed nodes
    placed = {n["id"] for n in t["nodes"] if n["x"] is not None}
    assert all(lk["n1"] in placed and lk["n2"] in placed for lk in t["links"])


# ------------------------------------------------------------------- the API
def test_api_endpoints():
    c = TestClient(create_app())
    assert c.get("/api/health").json()["ok"] is True
    sc = c.get("/api/scenario").json()
    assert "nodes" in sc and "plcs" in sc
    exp = c.get("/api/experiments").json()["experiments"]
    assert any(e["file"] == "e1_supervised.yaml" for e in exp)
    st = c.get("/api/run").json()
    assert st["running"] is False
    # static frontend is served
    assert c.get("/").status_code == 200
    assert c.get("/app.js").status_code == 200


def test_bad_scenario_is_400():
    c = TestClient(create_app())
    r = c.get("/api/scenario", params={"path": "/no/such/scenario.yaml"})
    assert r.status_code == 400
    assert "error" in r.json()


# ------------------------------------------------------------ authoring (P4)
def test_check_validity_good_model():
    pytest.importorskip("epanet")
    import scadavt.scenario as sc
    r = authoring.check_validity(sc.DEFAULT_SCENARIO)
    assert r["ok"] is True and r["error"] is None


def test_save_coordinates_roundtrip_and_validity():
    pytest.importorskip("epanet")
    ws = authoring.ensure_workspace("town_test")
    t = topology.build(ws)
    tank = next(n for n in t["nodes"] if n["kind"] == "tank")
    newx, newy = tank["x"] + 123.0, tank["y"] - 45.0
    res = authoring.save_coordinates(ws, {tank["id"]: [newx, newy]})
    assert res["ok"] is True and res.get("saved") == 1
    t2 = topology.build(ws)
    moved = next(n for n in t2["nodes"] if n["id"] == tank["id"])
    assert abs(moved["x"] - newx) < 0.5 and abs(moved["y"] - newy) < 0.5


def test_validate_attacks_gate():
    import scadavt.scenario as sc
    res = authoring.validate_attacks(sc.DEFAULT_SCENARIO, [
        {"id": "ok1", "kind": "fdi", "target": "PLC1", "tag": "T2_LVL",
         "mode": "const", "value": 6.5, "start_h": 8, "end_h": 16},
        {"id": "bad1", "kind": "fdi", "target": "PLC1", "tag": "NOPE",
         "start_h": 1, "end_h": 2}])
    by = {r["id"]: r for r in res}
    assert by["ok1"]["ok"] is True and "victim_ip" in by["ok1"]["resolved"]
    assert by["bad1"]["ok"] is False


def test_build_experiment_yaml():
    import yaml as _yaml
    text = authoring.build_experiment_yaml("myexp", [
        {"id": "a1", "kind": "dos", "target": "PLC1", "start_h": 2, "end_h": 5}])
    doc = _yaml.safe_load(text)
    assert doc["name"] == "myexp" and doc["attacks"][0]["kind"] == "dos"
    assert "deployment" in doc


def test_authoring_api():
    c = TestClient(create_app())
    ws = c.post("/api/workspace").json()["path"]
    assert c.post("/api/validate", json={"path": ws}).json()["ok"] is True
    av = c.post("/api/attacks/validate", json={"path": ws, "attacks": [
        {"kind": "command", "target": "PLC6", "tag": "V1_SET_O", "mode": "const",
         "value": 0, "start_h": 1, "end_h": 2}]}).json()
    assert av["results"][0]["ok"] is True
    exp = c.post("/api/attacks/experiment", json={"name": "e", "attacks": [
        {"kind": "dos", "target": "PLC1", "start_h": 1, "end_h": 2}]})
    assert exp.status_code == 200 and "kind: dos" in exp.text


# ---------------------------------------------- attack execution from GUI (P4+)
def test_victim_view_mapping():
    import scadavt.scenario as sc
    from scadavt.webui import jobs
    scen = sc.load()
    v = jobs._victim_view(scen, [{"kind": "fdi", "target": "PLC1", "tag": "T2_LVL"}])
    assert v["gt_col"] == "T2_level" and v["mtu_col"] == "PLC1.T2_LVL"


def test_attack_jobs_api_shape():
    c = TestClient(create_app())
    st = c.get("/api/attacks/run").json()
    assert st["state"] == "idle"
    assert c.get("/api/attacks/results").json() == {"results": []} or \
        "results" in c.get("/api/attacks/results").json()
    # launching with no attacks is rejected
    assert c.post("/api/attacks/run", json={"experiment": {"name": "x", "attacks": []}}).status_code == 409
    # reproduce of a missing run is a clean 404
    assert c.post("/api/attacks/reproduce", json={"name": "nope"}).status_code == 404
    # dataset with nothing finished is a clean 400
    assert c.post("/api/attacks/dataset").status_code == 400


# ------------------------------------------------------- live run (process)
class _FakeWS:
    def __init__(self):
        self.frames, self.status = [], []

    async def send_json(self, m):
        (self.frames if m.get("type") == "frame" else self.status).append(m)


def test_live_run_streams_supervised_frames(tmp_path):
    """A short supervised run must stream frames and keep tanks off the floor."""
    pytest.importorskip("epanet")

    async def scenario():
        mgr = RunManager()
        ws = _FakeWS()
        mgr.register(ws)
        st = await mgr.start({"hours": 4, "step": 1800, "tick": 0.1,
                              "seed": 1, "supervise": True})
        assert st["running"]
        await asyncio.sleep(3.5)
        status = mgr.status()
        rundir = mgr.rundir
        await mgr.stop()
        return ws, status, rundir

    ws, status, rundir = asyncio.run(scenario())
    assert status["error"] is None
    assert len(ws.frames) >= 3
    last = ws.frames[-1]["row"]
    levels = {k: last[k] for k in last if k.endswith("_level")}
    assert len(levels) == 6
    # supervision keeps tanks from draining to empty within the run
    assert all(v > 0.2 for v in levels.values())
    # clean up the run directory this test created
    import shutil
    if rundir and rundir.exists():
        shutil.rmtree(rundir, ignore_errors=True)
