"""L1 validation tests (Phase 2, Milestone 2) — the single, two-stage validator
that both the CLI and GUI call (R2), structure-then-authoritative-semantic (R7).
"""

from scadavt.core import validation, coregen, model


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


def test_valid_config_passes_both_stages():
    r = validation.check(_valid())
    assert r.ok and r.issues == []
    assert validation.validate(_valid()) == []
    assert validation.is_valid(_valid())


def test_structural_failure_short_circuits_semantic():
    cfg = _valid(); del cfg["devices"]        # breaks shape (required)
    r = validation.check(cfg)
    assert r.structural and r.semantic == []  # semantic skipped on bad shape
    assert all(i.startswith("structure:") for i in r.issues)


def test_schema_valid_but_semantically_broken_is_rejected():
    # duplicate IP: passes structure, fails the authoritative semantic validator
    cfg = _valid()
    cfg["mtu"]["ip"] = "10.0.0.1"
    r = validation.check(cfg)
    assert r.structural == []
    assert r.semantic and not r.ok
    assert any("semantic:" in i for i in r.issues)


def test_validate_matches_coregen_for_wellformed_config():
    # for a structurally-good config, the semantic issues are exactly coregen's
    cfg = _valid(); cfg["mapping"][0]["device"] = "GHOST"   # dangling reference
    assert validation.check(cfg).semantic == coregen.validate(cfg)


def test_ensure_valid_raises_on_bad_and_returns_on_good():
    good = _valid()
    assert validation.ensure_valid(good) is good
    bad = _valid(); bad["mtu"]["ip"] = "10.0.0.1"
    try:
        validation.ensure_valid(bad)
        assert False, "expected ValidationError"
    except validation.ValidationError as e:
        assert e.result.semantic


def test_result_bool_and_single_entry_point_identity():
    # the GUI and CLI must use this same function — guard that validate() is a
    # thin wrapper over check().issues (no divergent logic)
    cfg = _valid()
    assert bool(validation.check(cfg)) is True
    assert validation.validate(cfg) == validation.check(cfg).issues
