"""Tests for the research-lab web backend (scadavt.webui)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from scadavt.webui import epanet_topo, topology  # noqa: E402
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
