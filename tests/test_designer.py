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
    for f in ("index.html", "designer.js"):
        with open(os.path.join(DESIGNER_DIR, f)) as fh:
            blob += fh.read().lower()
    for bad in ("epanet", "apps.water", ".inp", "watersim", "hydraulic"):
        assert bad not in blob, f"designer references {bad!r}"


# ---- R8 lossless round-trip incl. GUI-style edit ---------------------------
def _arbitrary():
    """A hand-built NON-grid topology, the shape the designer emits."""
    return {
        "schema_version": 1, "name": "hand", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.1.254"},
        "devices": [
            {"id": "PLC1", "ip": "10.0.1.1", "protocol": "modbus", "seed": 2.0,
             "points": [{"id": "s", "role": "measurement"}, {"id": "a", "role": "command", "is_bool": True}]},
            {"id": "PLC2", "ip": "10.0.2.1", "protocol": "modbus", "seed": 5.0,
             "points": [{"id": "s", "role": "measurement"}, {"id": "a", "role": "command", "is_bool": True}]},
        ],
        "switches": [{"id": "SWA"}, {"id": "SWB"}],
        "routers": [{"id": "R1", "model": "router"}],
        "links": [
            {"a": "R1", "b": "SWA", "a_ip": "10.0.1.250"},
            {"a": "R1", "b": "SWB", "a_ip": "10.0.2.250"},
            {"a": "MTU", "b": "SWA", "a_ip": "10.0.1.254"},
            {"a": "PLC1", "b": "SWA", "a_ip": "10.0.1.1"},
            {"a": "PLC2", "b": "SWB", "a_ip": "10.0.2.1"},
        ],
        "mapping": [
            {"point": "s", "device": "PLC1", "role": "measurement", "element": "S1"},
            {"point": "a", "device": "PLC1", "role": "command", "element": "A1"},
            {"point": "s", "device": "PLC2", "role": "measurement", "element": "S2"},
            {"point": "a", "device": "PLC2", "role": "command", "element": "A2"},
        ],
        "rules": [{"id": "r1",
                   "if": [{"kind": "sensor", "comp": "S1", "attr": "value", "op": "below", "value": 3.0}],
                   "then": [{"comp": "A2", "kind": "pump", "attr": "status", "value": "open"}]}],
    }


def _gui_add_plc(cfg, pid, ip, subnet_switch):
    """Mirror designer.js addNode('plc') + addLink: the GUI's edit contract."""
    i = len(cfg["devices"]) + 1
    cfg["devices"].append({"id": pid, "ip": ip, "protocol": "modbus", "seed": 2.0,
                           "points": [{"id": "s", "role": "measurement"},
                                      {"id": "a", "role": "command", "is_bool": True}]})
    cfg["mapping"] += [{"point": "s", "device": pid, "role": "measurement", "element": f"S{i}"},
                       {"point": "a", "device": pid, "role": "command", "element": f"A{i}"}]
    cfg["links"].append({"a": pid, "b": subnet_switch, "a_ip": ip})


def test_roundtrip_with_edit_preserves_and_executes(tmp_path):
    cfg = _arbitrary()
    assert validation.is_valid(cfg)
    # edit as the GUI does: add a 3rd PLC on SWB + a cross-PLC rule
    _gui_add_plc(cfg, "PLC3", "10.0.2.2", "SWB")
    cfg["rules"].append({"id": "r2",
                         "if": [{"kind": "sensor", "comp": "S2", "attr": "value", "op": "below", "value": 3.0}],
                         "then": [{"comp": "A3", "kind": "pump", "attr": "status", "value": "open"}]})
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
