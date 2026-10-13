"""The deployment plan and the generated configuration files are consistent."""

import configparser
import ipaddress
from argparse import Namespace
from pathlib import Path

from scadavt import scenario as sc
from scadavt.deploy import compose as cp


def test_plan_addresses_unique_and_in_subnet():
    s = sc.load()
    p = cp.plan(s)
    ips = [a["lan"] for a in p["plcs"].values()] + [a["back"] for a in p["plcs"].values()]
    ips += [f["router_lan"] for f in p["fields"].values()]
    ips += [f["router_wan"] for f in p["fields"].values()]
    assert len(ips) == len(set(ips))
    for name, a in p["plcs"].items():
        net = ipaddress.ip_network(p["fields"][a["field"]]["subnet"])
        assert ipaddress.ip_address(a["lan"]) in net
    assert len(p["plcs"]) == 33 and len(p["fields"]) == 14


def test_generated_compose_and_configs(tmp_path: Path):
    s = sc.load()
    p = cp.plan(s)
    cp.write_configs(s, p, tmp_path)
    a = Namespace(out=tmp_path, image="scadavt-net:2.0", profile="full", period="1.0",
                  access_rate="2mbit", wan_delay="10ms", capture=False, project="t",
                  data=Path("data/consumption_per_person.csv"), start="2025-01-23", seed=1,
                  step=300, tick=1.0, hours=1.0)
    doc = cp.compose(s, p, a)
    assert len(doc["services"]) == 33 + 15 + 3
    assert len(doc["networks"]) == 14 + 3
    c2 = configparser.ConfigParser()
    c2.optionxform = str
    c2.read(tmp_path / "config" / "iomodulegate_tags.ini")
    assert len(c2.sections()) == len(s.all_tags())
    assert c2["T2_LVL"]["controller"] == "PLC1" and c2["T2_LVL"]["paraType"] == "i"
    assert doc["services"]["plc6"]["environment"]["GATEWAY"] == p["fields"]["F"]["router_lan"]
    assert doc["services"]["router_f"]["environment"]["LAN_RATE"] == "2mbit"
