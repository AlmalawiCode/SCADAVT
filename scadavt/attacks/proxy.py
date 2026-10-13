"""Transparent, Modbus/TCP-aware TCP proxy for the in-path attacker.

iptables REDIRECTs an intercepted MTU->PLC connection into this proxy; the
proxy finds the original destination (SO_ORIGINAL_DST), opens its own
connection to the real PLC, and relays both directions frame by frame, giving a
handler the chance to rewrite requests (MTU->PLC) and responses (PLC->MTU).

The handler is what makes an attack: freezing or offsetting a sensor value in
read responses is False Data Injection; altering a write request is command
manipulation; substituting a recorded response is replay. The proxy itself is
attack-agnostic.
"""

from __future__ import annotations

import socket
import struct
import threading

SOL_IP = 0
SO_ORIGINAL_DST = 80

# Modbus function codes the proxy understands (others pass through untouched)
FC_READ_HOLDING = 3
FC_READ_INPUT = 4
FC_WRITE_REGISTER = 6
FC_WRITE_REGISTERS = 16


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return buf
        buf += chunk
    return buf


def read_frame(sock) -> bytes | None:
    """Read one Modbus/TCP frame (MBAP header + PDU). None on EOF."""
    head = _recv_exact(sock, 7)
    if len(head) < 7:
        return None
    length = struct.unpack(">H", head[4:6])[0]
    rest = _recv_exact(sock, length - 1)
    if len(rest) < length - 1:
        return None
    return head + rest


def original_dst(sock) -> tuple[str, int]:
    data = sock.getsockopt(SOL_IP, SO_ORIGINAL_DST, 16)
    port = struct.unpack(">H", data[2:4])[0]
    ip = socket.inet_ntoa(data[4:8])
    return ip, port


class Handler:
    """Attack logic. Subclasses rewrite frames; the base class is a no-op."""

    def __init__(self):
        self.enabled = False
        self.state: dict = {}   # per-proxy scratch (e.g. txn -> request info)
        self.hits = 0

    def on_request(self, txn, unit, func, pdu: bytes) -> bytes:
        return pdu

    def on_response(self, txn, unit, func, pdu: bytes) -> bytes:
        return pdu


def _split(frame: bytes):
    txn, proto, length, unit = struct.unpack(">HHHB", frame[:7])
    func = frame[7]
    pdu = frame[7:]
    return txn, unit, func, pdu


def _rebuild(txn, unit, pdu: bytes) -> bytes:
    return struct.pack(">HHHB", txn, 0, len(pdu) + 1, unit) + pdu


class TransparentProxy:
    def __init__(self, listen_host: str, listen_port: int, handler: Handler,
                 log=lambda *a: None):
        self.host, self.port, self.handler, self.log = listen_host, listen_port, handler, log
        self._srv: socket.socket | None = None
        self._stop = threading.Event()

    def serve_forever(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(64)
        self._srv.settimeout(0.5)
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass

    def _handle(self, client: socket.socket):
        try:
            dst_ip, dst_port = original_dst(client)
        except OSError:
            client.close()
            return
        try:
            upstream = socket.create_connection((dst_ip, dst_port), timeout=5)
        except OSError:
            client.close()
            return
        t1 = threading.Thread(target=self._pump, args=(client, upstream, "req"), daemon=True)
        t2 = threading.Thread(target=self._pump, args=(upstream, client, "resp"), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()
        for s in (client, upstream):
            try:
                s.close()
            except OSError:
                pass

    def _pump(self, src: socket.socket, dst: socket.socket, direction: str):
        try:
            while not self._stop.is_set():
                frame = read_frame(src)
                if frame is None:
                    break
                try:
                    txn, unit, func, pdu = _split(frame)
                    if direction == "req":
                        pdu = self.handler.on_request(txn, unit, func, pdu)
                    else:
                        pdu = self.handler.on_response(txn, unit, func, pdu)
                    frame = _rebuild(txn, unit, pdu)
                except Exception:
                    pass  # on any parse error, forward the original frame
                dst.sendall(frame)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
