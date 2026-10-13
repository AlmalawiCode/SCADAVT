"""Packet capture wrapper (M3).

Thin control around ``tcpdump`` for capturing SCADA traffic on a CORE node or
interface to a pcap on the common timeline. Capturing live needs CAP_NET_RAW /
root and (for per-node capture) a running CORE node namespace, so it runs on a
CORE-capable host — not in this sandbox. The bundle (``bundle.py``) accepts a
pcap path whether produced here or elsewhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class PcapCapture:
    def __init__(self, out_path: str | Path, iface: str = "any",
                 bpf: str = "tcp", node_cmd: list | None = None):
        self.out_path = str(out_path)
        self.iface = iface
        self.bpf = bpf
        #: optional prefix to run tcpdump inside a CORE node, e.g.
        #: ``["vcmd", "-c", "/tmp/pycore.../PLC1"]`` or ``["ip", "netns", "exec", ns]``
        self.node_cmd = node_cmd or []
        self.proc: subprocess.Popen | None = None

    def available(self) -> bool:
        return shutil.which("tcpdump") is not None

    def start(self) -> None:
        cmd = list(self.node_cmd) + ["tcpdump", "-i", self.iface, "-w", self.out_path,
                                     "-U", self.bpf]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            self.proc = None
