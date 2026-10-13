"""Generate the networked deployment of a scenario as a Docker Compose project.

    python -m scadavt.deploy.compose --out build/town --profile validate
    docker compose -f build/town/docker-compose.yml up -d

Address plan (the same for every scenario):

    WAN backbone   10.100.0.0/24   control router .2, field router k at .(10+k),
                                   addresses .200-.250 left free for extra hosts
    field site k   10.10.k.0/24    router .254, its PLCs at .11, .12, ...
    control LAN    10.200.0.0/24   router .254, MTU .10
    back channel   172.16.0.0/16   PLCn at 172.16.1.n, IOModuleGate .0.10,
                                   WaterSystem server .0.20, MTU (HMI port) .0.30

The back channel plays the role of CORE's control network in the original
SCADAVT: it connects the external world (plant, gateway, HMI client) with the
nodes, and it is not routed into the emulated SCADA network.

Profiles choose the program that each node runs:

    validate  every PLC, the MTU and the gateway run a placeholder that only
              opens the expected ports; the WaterSystem server is real. Used to
              test the network before the SCADA programs exist.
    full      the SCADA programs described in docs/NETWORKED_INTERFACES.md.

Besides docker-compose.yml, the generator writes config/addresses.csv, the
two IOModuleGate configuration files of the paper (config/iomodulegate_plcs.ini
and config/iomodulegate_tags.ini), config/mtu_plcs.ini and config/tags.csv.
"""

from __future__ import annotations

import argparse
import configparser
import csv
from pathlib import Path

import yaml

from .. import scenario as sc
from ..protocols import wdsproto as wp

WAN = "10.100.0"
CTRL = "10.200.0"
BACK = "172.16"
MODBUS_PORT = 502
IO_PORT = 9161
WDS_PORT = 9000
HMI_PORT = 5020

ROUTER_SYSCTLS = {"net.ipv4.ip_forward": "1", "net.ipv4.conf.all.rp_filter": "0",
                  "net.ipv4.conf.default.rp_filter": "0"}

PROFILES = {
    "validate": {
        "plc": ["python", "-m", "scadavt.deploy.placeholder", "--name", "{name}",
                "--ports", f"{MODBUS_PORT},{IO_PORT}"],
        "mtu": ["python", "-m", "scadavt.deploy.placeholder", "--name", "MTU",
                "--ports", f"{HMI_PORT}"],
        "gateway": ["python", "-m", "scadavt.deploy.placeholder", "--name", "GATEWAY",
                    "--ports", ""],
    },
    "full": {
        "plc": ["python", "-m", "scadavt.field.plc", "--name", "{name}",
                "--modbus", f"0.0.0.0:{MODBUS_PORT}", "--io", f"0.0.0.0:{IO_PORT}"],
        "mtu": ["python", "-m", "scadavt.master.mtu", "--plcs", "/config/mtu_plcs.ini",
                "--hmi", f"0.0.0.0:{HMI_PORT}", "--historian", "/logs/historian.csv",
                "--connections", "/logs/connections.csv", "--period", "{period}"],
        "gateway": ["python", "-m", "scadavt.gateway.bridge",
                    "--plcs", "/config/iomodulegate_plcs.ini",
                    "--tags", "/config/iomodulegate_tags.ini",
                    "--wds", f"{BACK}.0.20:{WDS_PORT}", "--period", "{period}",
                    "--log", "/logs/gateway.csv"],
    },
}


def plan(scen: sc.Scenario) -> dict:
    """IP addresses of every node."""
    fields = list(scen.fields)
    p = {"fields": {}, "plcs": {}}
    for k, f in enumerate(fields, start=1):
        p["fields"][f] = {"index": k, "subnet": f"10.10.{k}.0/24", "router_lan": f"10.10.{k}.254",
                          "router_wan": f"{WAN}.{10 + k}"}
        for j, name in enumerate(scen.fields[f], start=11):
            n = int(name[3:])
            p["plcs"][name] = {"field": f, "lan": f"10.10.{k}.{j}", "back": f"{BACK}.1.{n}"}
    p["ctrl"] = {"router_lan": f"{CTRL}.254", "router_wan": f"{WAN}.2", "mtu": f"{CTRL}.10",
                 "mtu_back": f"{BACK}.0.30"}
    p["gateway"] = f"{BACK}.0.10"
    p["wds"] = f"{BACK}.0.20"
    return p


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _ini() -> configparser.ConfigParser:
    c = configparser.ConfigParser()
    c.optionxform = str  # keep "paraType" as written in the paper
    return c


def write_configs(scen: sc.Scenario, p: dict, out: Path) -> None:
    cfg = out / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    # IOModuleGate file 1: where each field device listens on the back channel
    c1 = _ini()
    for name, a in p["plcs"].items():
        c1[name] = {"ip": a["back"], "port": str(IO_PORT)}
    with open(cfg / "iomodulegate_plcs.ini", "w") as fh:
        c1.write(fh)
    # IOModuleGate file 2: which device supervises each process parameter
    c2 = _ini()
    for t in scen.all_tags():
        c2[t.name] = {"controller": t.plc, "paraType": t.direction, "element": t.element,
                      "elementType": "node" if t.ptype == wp.PT_NODE else "link",
                      "dataType": wp.DATA_TYPE_NAMES[t.dtype]}
    with open(cfg / "iomodulegate_tags.ini", "w") as fh:
        c2.write(fh)
    # MTU: where each PLC listens on the emulated SCADA network
    c3 = _ini()
    for name, a in p["plcs"].items():
        c3[name] = {"ip": a["lan"], "port": str(MODBUS_PORT), "unit": "1"}
    with open(cfg / "mtu_plcs.ini", "w") as fh:
        c3.write(fh)
    with open(cfg / "tags.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["plc", "tag", "element", "element_type", "data_type", "direction",
                    "register_type", "address"])
        for t in scen.all_tags():
            w.writerow([t.plc, t.name, t.element, "node" if t.ptype == wp.PT_NODE else "link",
                        wp.DATA_TYPE_NAMES[t.dtype], t.direction, t.reg_type, t.address])
    with open(cfg / "addresses.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["node", "network", "ip"])
        for f, a in p["fields"].items():
            w.writerow([f"router_{f}", f"field_{f}", a["router_lan"]])
            w.writerow([f"router_{f}", "wan", a["router_wan"]])
        w.writerow(["router_ctrl", "ctrl", p["ctrl"]["router_lan"]])
        w.writerow(["router_ctrl", "wan", p["ctrl"]["router_wan"]])
        for name, a in p["plcs"].items():
            w.writerow([name.lower(), f"field_{a['field']}", a["lan"]])
            w.writerow([name.lower(), "back", a["back"]])
        w.writerow(["mtu", "ctrl", p["ctrl"]["mtu"]])
        w.writerow(["mtu", "back", p["ctrl"]["mtu_back"]])
        w.writerow(["gateway", "back", p["gateway"]])
        w.writerow(["wds", "back", p["wds"]])


def _cmd(template, **kw):
    return [x.format(**kw) for x in template]


def compose(scen: sc.Scenario, p: dict, a) -> dict:
    image = a.image
    prof = PROFILES[a.profile]
    vols = [f"{a.out.resolve() / 'config'}:/config:ro", f"{a.out.resolve() / 'logs'}:/logs"]
    caps = ["NET_ADMIN", "NET_RAW"]
    nets = {"wan": {"ipam": {"config": [{"subnet": f"{WAN}.0/24"}]}},
            "ctrl": {"ipam": {"config": [{"subnet": f"{CTRL}.0/24"}]}},
            "back": {"ipam": {"config": [{"subnet": f"{BACK}.0.0/16"}]}}}
    svc = {}
    field_routes = f"{CTRL}.0/24 via {p['ctrl']['router_wan']}"
    for f, fa in p["fields"].items():
        nets[f"field_{f}"] = {"ipam": {"config": [{"subnet": fa["subnet"]}]}}
        env = {"ROUTES": field_routes, "LAN_SUBNET": fa["subnet"]}
        if a.access_rate:
            env["LAN_RATE"] = a.access_rate
        if a.wan_delay:
            env["WAN_DELAY"] = a.wan_delay
        if a.capture:
            env["CAPTURE"] = "1"
        svc[f"router_{f.lower()}"] = {
            "image": image, "hostname": f"router_{f.lower()}", "cap_add": caps,
            "entrypoint": ["/opt/scadavt-net/router.sh"], "environment": env,
            "sysctls": ROUTER_SYSCTLS, "volumes": vols[1:],
            "networks": {"wan": {"ipv4_address": fa["router_wan"]},
                         f"field_{f}": {"ipv4_address": fa["router_lan"]}}}
    ctrl_routes = ";".join(f"{fa['subnet']} via {fa['router_wan']}" for fa in p["fields"].values())
    env = {"ROUTES": ctrl_routes, "LAN_SUBNET": f"{CTRL}.0/24"}
    if a.wan_delay:
        env["WAN_DELAY"] = a.wan_delay
    if a.capture:
        env["CAPTURE"] = "1"
    svc["router_ctrl"] = {
        "image": image, "hostname": "router_ctrl", "cap_add": caps,
        "entrypoint": ["/opt/scadavt-net/router.sh"], "environment": env,
        "sysctls": ROUTER_SYSCTLS, "volumes": vols[1:],
        "networks": {"wan": {"ipv4_address": p["ctrl"]["router_wan"]},
                     "ctrl": {"ipv4_address": p["ctrl"]["router_lan"]}}}
    for name, pa in p["plcs"].items():
        fa = p["fields"][pa["field"]]
        svc[name.lower()] = {
            "image": image, "hostname": name.lower(), "cap_add": caps,
            "environment": {"GATEWAY": fa["router_lan"], "SCADAVT_NODE": name},
            "command": _cmd(prof["plc"], name=name, period=a.period), "volumes": vols,
            "depends_on": [f"router_{pa['field'].lower()}"],
            "networks": {f"field_{pa['field']}": {"ipv4_address": pa["lan"]},
                         "back": {"ipv4_address": pa["back"]}}}
    svc["mtu"] = {
        "image": image, "hostname": "mtu", "cap_add": caps,
        "environment": {"GATEWAY": p["ctrl"]["router_lan"], "SCADAVT_NODE": "MTU"},
        "command": _cmd(prof["mtu"], period=a.period), "volumes": vols,
        "depends_on": ["router_ctrl"],
        "networks": {"ctrl": {"ipv4_address": p["ctrl"]["mtu"]},
                     "back": {"ipv4_address": p["ctrl"]["mtu_back"]}}}
    svc["wds"] = {
        "image": image, "hostname": "wds", "entrypoint": [],
        "command": ["python", "-m", "scadavt.wds.server", "--listen", f"0.0.0.0:{WDS_PORT}",
                    "--start", a.start, "--seed", str(a.seed), "--step", str(a.step),
                    "--tick", str(a.tick), "--hours", str(a.hours),
                    "--log", "/logs/ground_truth.csv"],
        "volumes": vols + [f"{a.data.resolve()}:/app/data/consumption_per_person.csv:ro"],
        "networks": {"back": {"ipv4_address": p["wds"]}}}
    svc["gateway"] = {
        "image": image, "hostname": "gateway", "cap_add": caps,
        "command": _cmd(prof["gateway"], period=a.period), "volumes": vols,
        "depends_on": ["wds"] + [n.lower() for n in p["plcs"]],
        "networks": {"back": {"ipv4_address": p["gateway"]}}}
    return {"name": a.project, "services": svc, "networks": nets}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default=str(sc.DEFAULT_SCENARIO))
    ap.add_argument("--out", type=Path, default=Path("build/town"))
    ap.add_argument("--profile", choices=list(PROFILES), default="validate")
    ap.add_argument("--project", default="scadavt")
    ap.add_argument("--image", default="scadavt-net:2.0")
    ap.add_argument("--data", type=Path, default=Path("data/consumption_per_person.csv"))
    ap.add_argument("--start", default="2025-01-23")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--step", type=int, default=300, help="simulated seconds per plant tick")
    ap.add_argument("--tick", type=float, default=1.0, help="wall seconds per plant tick")
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--period", default="1.0", help="MTU and gateway cycle (wall seconds)")
    ap.add_argument("--access-rate", default="", help="rate of each field access link, e.g. 2mbit")
    ap.add_argument("--wan-delay", default="", help="one-way delay on the WAN side, e.g. 10ms")
    ap.add_argument("--capture", action="store_true", help="pcap on every router")
    a = ap.parse_args(argv)
    scen = sc.load(a.scenario)
    p = plan(scen)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "logs").mkdir(exist_ok=True)
    write_configs(scen, p, a.out)
    doc = compose(scen, p, a)
    (a.out / "docker-compose.yml").write_text(
        "# Generated by scadavt.deploy.compose - do not edit, regenerate instead.\n"
        + yaml.dump(doc, Dumper=_NoAliasDumper, sort_keys=False, width=120))
    print(f"{len(doc['services'])} services, {len(doc['networks'])} networks -> "
          f"{a.out / 'docker-compose.yml'}")


if __name__ == "__main__":
    main()
