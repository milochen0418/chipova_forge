"""The board's analog side: ngspice's shared library (libngspice), driven step by step.

Standard library only (ctypes), for the session agent. The circuit's inputs are ngspice
"external" sources whose values Forge sets (the chip pins that switch things, the environment's
settings); ngspice asks for them at every time point it solves. ``advance(t)`` runs the transient
analysis on to virtual time ``t`` and stops there (a breakpoint), so the circuit keeps pace with
Renode's board; ``value(node)`` reads a node's voltage at the last point. (``ngSpice_SetBkpt``
doesn't stop a foreground run in ngspice 44; a ``stop when`` condition does.)
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import CFUNCTYPE, POINTER, Structure, c_bool, c_char_p, c_double, c_int, c_short, c_void_p


class _VectorInfo(Structure):
    _fields_ = [("v_name", c_char_p), ("v_type", c_int), ("v_flags", c_short), ("v_realdata", POINTER(c_double)),
                ("v_compdata", c_void_p), ("v_length", c_int)]


_SendChar = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)
_SendStat = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)
_ControlledExit = CFUNCTYPE(c_int, c_int, c_bool, c_bool, c_int, c_void_p)
_BGThreadRunning = CFUNCTYPE(c_int, c_bool, c_int, c_void_p)
_GetSourceData = CFUNCTYPE(c_int, POINTER(c_double), c_double, c_char_p, c_int, c_void_p)


class SpiceError(RuntimeError):
    pass


class Ngspice:
    """One circuit in ngspice's shared library (a process holds one)."""

    LIBRARY = "libngspice.so.0"

    def __init__(self, netlist: list[str], sources: dict[str, float]) -> None:
        self.lib = ctypes.CDLL(self.LIBRARY)
        self.lib.ngGet_Vec_Info.restype = POINTER(_VectorInfo)
        self.lib.ngGet_Vec_Info.argtypes = [c_char_p]
        self.lib.ngSpice_Command.argtypes = [c_char_p]
        self.lib.ngSpice_SetBkpt.argtypes = [c_double]
        self.lib.ngSpice_SetBkpt.restype = c_bool
        self.sources = {k.lower(): float(v) for k, v in sources.items()}
        self.lock = threading.Lock()
        self.output: list[str] = []
        self.exited = False
        self.t = 0.0
        self._started = False
        self._callbacks = [
            _SendChar(self._print), _SendStat(lambda s, i, u: 0), _ControlledExit(self._exit),
            _BGThreadRunning(lambda running, i, u: 0), _GetSourceData(self._source), _GetSourceData(self._source),
        ]
        cb = self._callbacks
        self.lib.ngSpice_Init(cb[0], cb[1], cb[2], None, None, cb[3], None)
        ident = c_int(0)
        self.lib.ngSpice_Init_Sync(cb[4], cb[5], None, ctypes.byref(ident), None)
        lines = [ln.encode() for ln in netlist] + [None]
        arr = (c_char_p * len(lines))(*lines)
        if self.lib.ngSpice_Circ(arr) != 0 or self.exited:
            raise SpiceError("ngspice didn't take the circuit: " + " | ".join(self.output[-8:]))

    def _print(self, text: bytes, ident: int, user) -> int:  # noqa: ANN001
        s = (text or b"").decode(errors="replace")
        self.output.append(s)
        if len(self.output) > 500:
            del self.output[:250]
        return 0

    def _exit(self, status: int, unload: bool, quit_: bool, ident: int, user) -> int:  # noqa: ANN001
        self.exited = True
        return 0

    def _source(self, value, t: float, name: bytes, ident: int, user) -> int:  # noqa: ANN001
        value[0] = self.sources.get((name or b"").decode().lower(), 0.0)
        return 0

    def set(self, source: str, value: float) -> None:
        self.sources[source.lower()] = float(value)

    def advance(self, t: float) -> None:
        """Solves the circuit on to time ``t`` (s) and stops at the first time point past it (at
        most one maximum step, ``.tran``'s last number, later). ngspice's own stop condition does
        it: the old one is deleted first (they add up), then the transient goes on."""
        if t <= self.t:
            return
        with self.lock:
            self.lib.ngSpice_Command(b"delete all")
            self.lib.ngSpice_Command(f"stop when time > {t:.9f}".encode())
            self.lib.ngSpice_Command(b"resume" if self._started else b"run")
            self._started = True
            self.t = t
        if self.exited:
            raise SpiceError("ngspice stopped: " + " | ".join(self.output[-8:]))

    def value(self, vector: str) -> float:
        """The last value of a vector (a node's voltage, ``temp`` or ``v(temp)``)."""
        with self.lock:
            info = self.lib.ngGet_Vec_Info(vector.encode())
            if not info or info.contents.v_length <= 0 or not info.contents.v_realdata:
                raise SpiceError(f"the circuit has no {vector!r}")
            return float(info.contents.v_realdata[info.contents.v_length - 1])

    def time(self) -> float:
        return self.value("time")
