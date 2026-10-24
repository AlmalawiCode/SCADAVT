"""HTTP service tests (Phase 2, Milestone 5).

Cover the thin wrapper endpoints over L1/L2 and the R10 security model: token
auth, cross-origin (CSRF) rejection of state-changing requests even for a local
browser, mandatory token for a non-local bind, and graceful /run without CORE.
The service must remain a leaf (R5) — core/CLI never import it.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from scadavt.service import create_app


def _valid():
    return {
        "name": "t", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus",
                     "points": [{"id": "s", "role": "measurement"},
                                {"id": "a", "role": "command", "is_bool": True}]}],
        "switches": [{"id": "SW"}],
        "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
                  {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
        "mapping": [{"point": "s", "device": "PLC1", "role": "measurement", "element": "S1"}],
        "rules": [],
    }


def test_health_open():
    c = TestClient(create_app())
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_validate_ok_and_bad():
    c = TestClient(create_app())
    assert c.post("/validate", json={"config": _valid()}).json()["ok"] is True
    bad = _valid(); bad["mtu"]["ip"] = "10.0.0.1"          # dup IP
    out = c.post("/validate", json={"config": bad}).json()
    assert out["ok"] is False and out["issues"]


def test_plan_returns_topology():
    c = TestClient(create_app())
    r = c.post("/plan", json={"config": _valid()})
    assert r.status_code == 200
    ids = {n["id"] for n in r.json()["nodes"]}
    assert {"PLC1", "MTU", "SW"} <= ids


def test_plan_rejects_invalid():
    c = TestClient(create_app())
    bad = _valid(); bad["mtu"]["ip"] = "10.0.0.1"
    assert c.post("/plan", json={"config": bad}).status_code == 422


def test_build_writes_artifacts(tmp_path):
    c = TestClient(create_app())
    r = c.post("/build", json={"config": _valid()})
    assert r.status_code == 200
    import os
    assert "session.json" in r.json()["files"]
    assert os.path.exists(os.path.join(r.json()["out"], "session.json"))


# --- R10 security -------------------------------------------------------
def test_token_required_when_configured():
    c = TestClient(create_app(token="secret"))
    # no token header -> 401 on a POST
    assert c.post("/validate", json={"config": _valid()}).status_code == 401
    # correct token -> ok
    r = c.post("/validate", json={"config": _valid()},
               headers={"X-SCADAVT-Token": "secret"})
    assert r.status_code == 200
    # wrong token -> 401
    assert c.post("/validate", json={"config": _valid()},
                  headers={"X-SCADAVT-Token": "nope"}).status_code == 401


def test_get_health_open_even_with_token():
    c = TestClient(create_app(token="secret"))
    assert c.get("/health").status_code == 200       # read-only stays convenient


def test_cross_origin_state_change_rejected_even_locally():
    # a drive-by page in a local browser (hostile Origin) must not drive POSTs
    c = TestClient(create_app())
    r = c.post("/validate", json={"config": _valid()},
               headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_allowlisted_origin_passes():
    c = TestClient(create_app(cors_origins=["http://127.0.0.1:8099"]))
    r = c.post("/validate", json={"config": _valid()},
               headers={"Origin": "http://127.0.0.1:8099"})
    assert r.status_code == 200


def test_non_browser_client_without_origin_passes():
    c = TestClient(create_app())                      # no Origin header (curl/CLI)
    assert c.post("/validate", json={"config": _valid()}).status_code == 200


def test_non_local_bind_requires_token():
    with pytest.raises(ValueError):
        create_app(bind_host="0.0.0.0")               # refuse unauthenticated exposure
    # with a token it is allowed
    app = create_app(bind_host="0.0.0.0", token="secret")
    assert app is not None


def test_run_without_core_is_503(monkeypatch):
    # force the live engine unavailable AND no delegate -> graceful 503, not a crash
    import scadavt.service.app as appmod
    monkeypatch.delenv("SCADAVT_CORE_PYTHON", raising=False)
    monkeypatch.setattr(appmod, "_engine_available", lambda: False)
    c = TestClient(create_app())
    r = c.post("/run", json={"config": _valid()})
    assert r.status_code == 503


def test_run_delegates_to_core_python(monkeypatch, tmp_path):
    # with SCADAVT_CORE_PYTHON set, /run shells out to that interpreter and
    # returns its JSON — tested with a fake "core python" that ignores its args
    # and prints a canned results line (so no real CORE is needed).
    fake = tmp_path / "fake_core_python"
    fake.write_text('#!/usr/bin/env python3\n'
                    'import sys\n'
                    'print(\'{"started": true, "reached": 1, "devices": 1, '
                    '"results": [{"id": "PLC1", "reachable": true, "points": {"a": 1.0}}]}\')\n')
    fake.chmod(0o755)
    monkeypatch.setenv("SCADAVT_CORE_PYTHON", str(fake))
    c = TestClient(create_app())
    r = c.post("/run", json={"config": _valid()})
    assert r.status_code == 200
    body = r.json()
    assert body["reached"] == 1 and body["results"][0]["points"]["a"] == 1.0


def test_service_is_water_free():
    # R6-adjacent: the service wraps generic L1/L2, no water/EPANET import
    import inspect, scadavt.service.app as appmod
    src = inspect.getsource(appmod)
    assert "apps.water" not in src and "epanet" not in src.lower()
