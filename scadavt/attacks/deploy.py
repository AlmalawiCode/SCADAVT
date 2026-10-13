"""Inject isolated attacker containers into a generated deployment.

Each attack becomes one attacker service attached only to the victim's field
network (never the back channel or the control LAN), so by construction it can
act only on that one SCADA segment. The attacker runs the MITM agent
(``scadavt.attacks.mitm``) and is driven by a control file in the shared logs
volume that the experiment runner toggles at the window boundaries.
"""

from __future__ import annotations

from .. import scenario as sc
from ..deploy.compose import ROUTER_SYSCTLS
from . import catalog

ATTACKER_SYSCTLS = {"net.ipv4.ip_forward": "1", **{k: v for k, v in ROUTER_SYSCTLS.items()
                                                    if "rp_filter" in k}}


def _field_ip(subnet: str, host: int) -> str:
    base = subnet.rsplit(".", 1)[0]  # "10.10.k.0/24" -> "10.10.k"
    return f"{base}.{host}"


def attacker_services(scen: sc.Scenario, plan: dict, attacks, image: str,
                      logs_vol: str, logs_dir: str = "attacks", ip_base: int = 50) -> dict:
    """Return ``{service_name: service_dict}`` for every attack."""
    svc: dict[str, dict] = {}
    for i, atk in enumerate(attacks):
        r = catalog.resolve(atk, scen, plan)
        ip = _field_ip(r["subnet"], ip_base + i)
        name = f"attacker_{atk.id}"
        svc[name] = {
            "image": image,
            "hostname": name,
            "cap_add": ["NET_ADMIN", "NET_RAW"],
            "sysctls": ATTACKER_SYSCTLS,
            "environment": {"GATEWAY": r["router_ip"], "SCADAVT_NODE": name.upper()},
            "command": [
                "python", "-m", "scadavt.attacks.mitm",
                "--victim-ip", r["victim_ip"], "--gw-ip", r["router_ip"],
                "--func", str(r["func"]), "--address", str(r["address"]),
                "--mode", atk.mode, "--value", str(atk.value),
                "--control", f"/logs/{logs_dir}/{atk.id}.json",
                "--log", f"/logs/{logs_dir}/{atk.id}.csv",
            ],
            "volumes": [logs_vol],
            "depends_on": [f"router_{r['field'].lower()}", atk.plc.lower()],
            "networks": {f"field_{r['field']}": {"ipv4_address": ip}},
        }
    return svc
