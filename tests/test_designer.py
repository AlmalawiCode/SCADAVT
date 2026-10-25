"""Generic designer tests (Phase 2, Milestone 6).

The designer's document IS the canonical config, so its core guarantees are
testable at the model level:

* **served + reachable** — the service serves the static designer same-origin;
* **R6 water-free** — the designer carries no water/EPANET logic;
* **R8 round-trip incl. edit** — a Python-authored config loaded, *edited* the
  way the GUI edits (add PLC + link + rule), exported and reloaded loses nothing
  and still validates/plans;
* **R9 arbitrary topology** — a hand-built non-grid config (not from grid()) is
  valid and resolves to a correct plan, i.e. the engine makes no grid assumption.

A real-browser end-to-end (build a topology in the GUI → export → validate/run)
is exercised separately against a running service; see docs/PHASE2_DESIGN notes.
"""

import os

import pytest

from scadavt.core import model, validation

DESIGNER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scadavt", "designer", "static")


# ---- served + reachable -----------------------------------------------------
def test_designer_served_same_origin():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from scadavt.service import create_app
    c = TestClient(create_app())
    r = c.get("/designer/")
    assert r.status_code == 200 and "SCADAVT" in r.text
    assert c.get("/designer/designer.js").status_code == 200


def test_same_origin_post_allowed():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from scadavt.service import create_app
    c = TestClient(create_app())
    # the served page posts with a same-origin Origin header -> must be allowed
    r = c.post("/validate", json={"config": _arbitrary()},
               headers={"Origin": "http://testserver", "Host": "testserver"})
    assert r.status_code == 200


# ---- R6 water-free ----------------------------------------------------------
def test_designer_is_domain_neutral():
    blob = ""
    for f in ("index.html", "designer.js", "tutorial.html"):
        with open(os.path.join(DESIGNER_DIR, f)) as fh:
            blob += fh.read().lower()
    for bad in ("epanet", "apps.water", ".inp", "watersim", "hydraulic"):
        assert bad not in blob, f"designer references {bad!r}"


def test_tutorial_served_in_new_tab():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from scadavt.service import create_app
    c = TestClient(create_app())
    r = c.get("/designer/tutorial.html")
    assert r.status_code == 200
    assert "three" in r.text.lower() and "PLC" in r.text        # worked 3-PLC example present
    # the designer links to it and opens it in a new tab
    with open(os.path.join(DESIGNER_DIR, "designer.js")) as fh:
        js = fh.read()
    assert 'window.open("tutorial.html"' in js and "_blank" in js


# ---- R8 lossless round-trip incl. GUI-style edit ---------------------------
def _plc(pid, ip):
    """Register-native PLC — the shape the designer now emits (no sensor/actuator)."""
    return {"id": pid, "ip": ip, "protocol": "modbus", "seed": 2.0,
            "registers": [{"id": "s", "area": "input_register", "data_type": "float32", "initial": 2.0},
                          {"id": "a", "area": "coil", "data_type": "bool", "owner": "mtu"}]}


def _arbitrary():
    """A hand-built NON-grid topology, register-native, as the designer emits."""
    return {
        "schema_version": 1, "name": "hand", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.1.254"},
        "devices": [_plc("PLC1", "10.0.1.1"), _plc("PLC2", "10.0.2.1")],
        "switches": [{"id": "SWA"}, {"id": "SWB"}],
        "routers": [{"id": "R1", "model": "router"}],
        "links": [
            {"a": "R1", "b": "SWA", "a_ip": "10.0.1.250"},
            {"a": "R1", "b": "SWB", "a_ip": "10.0.2.250"},
            {"a": "MTU", "b": "SWA", "a_ip": "10.0.1.254"},
            {"a": "PLC1", "b": "SWA", "a_ip": "10.0.1.1"},
            {"a": "PLC2", "b": "SWB", "a_ip": "10.0.2.1"},
        ],
        "logic": [{"id": "r1",
                   "if": [{"device": "PLC1", "register": "s", "op": "below", "value": 3.0}],
                   "then": [{"device": "PLC2", "register": "a", "set": 1}]}],
    }


def _gui_add_plc(cfg, pid, ip, subnet_switch):
    """Mirror designer.js addNode('plc') + addLink: the GUI's edit contract."""
    cfg["devices"].append(_plc(pid, ip))
    cfg["links"].append({"a": pid, "b": subnet_switch, "a_ip": ip})


def test_roundtrip_with_edit_preserves_and_executes(tmp_path):
    cfg = _arbitrary()
    assert validation.is_valid(cfg)
    # edit as the GUI does: add a 3rd PLC on SWB + a cross-PLC logic rule
    _gui_add_plc(cfg, "PLC3", "10.0.2.2", "SWB")
    cfg["logic"].append({"id": "r2",
                         "if": [{"device": "PLC2", "register": "s", "op": "below", "value": 3.0}],
                         "then": [{"device": "PLC3", "register": "a", "set": 1}]})
    # export -> reload (Python <- GUI document) is lossless
    p = tmp_path / "net.json"
    model.dump(cfg, str(p))
    back = model.load(str(p))
    assert back == cfg                                   # nothing lost
    # the edited config is still valid and executable (plan resolves)
    assert validation.validate(back) == []
    from scadavt.core import corelive
    plan = corelive.build_plan(back)
    ids = {n["id"] for n in plan["nodes"]}
    assert {"PLC1", "PLC2", "PLC3", "MTU", "R1", "SWA", "SWB"} == ids


# ---- A5: one register model behind API and GUI -----------------------------
def test_designer_config_is_register_native():
    cfg = _arbitrary()
    for d in cfg["devices"]:
        assert "registers" in d and "points" not in d
        assert {r["id"]: r["area"] for r in d["registers"]} == {"s": "input_register", "a": "coil"}
    assert "mapping" not in cfg and "rules" not in cfg and cfg["logic"]
    assert validation.validate(cfg) == []


def test_api_and_gui_share_register_model(tmp_path):
    # a config authored by the Python API (corelive.grid) is register-native and
    # round-trips through the designer's document format unchanged (API == GUI).
    from scadavt.core import corelive
    api_cfg = corelive.grid(3)
    api_cfg.pop("_plc_ips", None); api_cfg.pop("_seg_of", None)
    assert all("registers" in d for d in api_cfg["devices"]) and api_cfg["logic"]
    p = tmp_path / "net.json"
    model.dump(api_cfg, str(p))
    assert model.load(str(p)) == api_cfg            # opens in the designer unchanged
    assert validation.validate(api_cfg) == []


# ---- R9 arbitrary (non-grid) topology --------------------------------------
def test_arbitrary_non_grid_topology_valid_and_planned():
    cfg = _arbitrary()
    assert validation.validate(cfg) == []
    from scadavt.core import corelive
    plan = corelive.build_plan(cfg)
    # asymmetric: PLC1 on SWA (with MTU), PLC2 on SWB, joined by a router
    assert plan["routers"] == ["R1"]
    assert plan["gateways"]["PLC1"].endswith(".250")
    assert plan["gateways"]["PLC2"].rsplit(".", 1)[0] != plan["gateways"]["PLC1"].rsplit(".", 1)[0]
