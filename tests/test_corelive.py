"""Offline tests for the generic live-network driver (scadavt.core.corelive).

These assert that a network of *arbitrary size* resolves into a correct CORE
build plan — any N PLCs, several /24 subnets, one router, one MTU, and a
cross-PLC control ring — with no per-scenario code and without needing a CORE
daemon. The live build()/start() path is exercised by tools/live_core_network.py
on a CORE host.
"""

from scadavt.core import corelive, coregen


def _ip_ifaces(plan):
    """All (node implied by position) interface IP strings assigned in the plan."""
    out = []
    for lk in plan["links"]:
        for side in ("a", "b"):
            f = lk[f"{side}_iface"]
            if f["ip"]:
                out.append(f["ip"])
    return out


def test_grid_config_validates_for_many_sizes():
    for n in (1, 3, 8, 20, 50):
        cfg = corelive.grid(n, per_subnet=8)
        assert coregen.validate(cfg) == [], (n, coregen.validate(cfg))
        assert len(cfg["devices"]) == n
        assert cfg["mtu"]["id"] == "MTU"


def test_plan_node_and_link_counts_scale():
    # 20 PLCs, 8 per subnet -> 3 subnets -> 3 switches + 1 router + 1 MTU + 20 PLCs
    cfg = corelive.grid(20, per_subnet=8)
    plan = corelive.build_plan(cfg)
    kinds = {}
    for n in plan["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    assert kinds == {"plc": 20, "mtu": 1, "router": 1, "switch": 3}
    # links: 20 PLC->switch + 1 MTU->switch + 3 router->switch legs = 24
    assert len(plan["links"]) == 24


def test_every_plc_and_mtu_get_a_unique_ip_switches_bare():
    cfg = corelive.grid(20, per_subnet=8)
    plan = corelive.build_plan(cfg)
    ips = _ip_ifaces(plan)
    # 20 PLC IPs + 1 MTU IP + 3 router-leg IPs = 24 addressed interfaces, all unique
    assert len(ips) == 24
    assert len(set(ips)) == 24
    # switch ends carry no IP
    sw_ids = {n["id"] for n in plan["nodes"] if n["kind"] == "switch"}
    for lk in plan["links"]:
        for side in ("a", "b"):
            if lk[side] in sw_ids:
                assert lk[f"{side}_iface"]["ip"] is None


def test_router_joins_subnets_and_gives_every_endpoint_a_gateway():
    cfg = corelive.grid(20, per_subnet=8)
    plan = corelive.build_plan(cfg)
    assert plan["routers"] == ["R1"]
    # every PLC and the MTU must get a default gateway (= its subnet's router leg)
    endpoints = {d["id"] for d in cfg["devices"]} | {"MTU"}
    assert set(plan["gateways"]) == endpoints
    # each gateway is the .250 leg on that endpoint's own /24
    for nid, gw in plan["gateways"].items():
        ip = cfg["_plc_ips"].get(nid) or cfg["mtu"]["ip"]
        assert ip.rsplit(".", 1)[0] == gw.rsplit(".", 1)[0]
        assert gw.endswith(".250")


def test_cross_plc_ring_targets_the_next_plc():
    # register-native: PLC i's input register 's' low -> set PLC i+1's coil 'a'
    cfg = corelive.grid(12, per_subnet=6)
    assert "rules" not in cfg and "mapping" not in cfg
    logic = {r["id"]: r for r in cfg["logic"]}
    assert len(logic) == 12
    assert logic["r1"]["if"][0] == {"device": "PLC1", "register": "s", "op": "below", "value": 3.0}
    assert logic["r1"]["then"][0] == {"device": "PLC2", "register": "a", "set": 1}
    assert logic["r12"]["then"][0] == {"device": "PLC1", "register": "a", "set": 1}


def test_interface_indices_are_per_node_sequential():
    # a node on two links (none here reuse) — the router has one iface per subnet
    cfg = corelive.grid(16, per_subnet=8)       # 2 subnets -> router has 2 legs
    plan = corelive.build_plan(cfg)
    r_idx = []
    for lk in plan["links"]:
        if lk["a"] == "R1":
            r_idx.append(lk["a_iface"]["idx"])
        if lk["b"] == "R1":
            r_idx.append(lk["b_iface"]["idx"])
    assert r_idx == [0, 1]                       # two legs, indices 0 then 1
