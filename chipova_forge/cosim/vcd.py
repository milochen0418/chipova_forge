"""Reading a VCD waveform (IEEE 1364 value change dump) for the browser's waveform viewer.

:func:`load` reads the whole file once (up to MAX_BYTES) into each signal's list of changes and
keeps the last few in memory; :func:`window` gives the changes of some signals between two times,
with the value each had at the start, and thins out signals that change far more often than the
screen can show.
"""

from __future__ import annotations

import bisect
import collections
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

MAX_BYTES = 256 * 1024 * 1024
MAX_POINTS = 4000  # per signal and window; more are thinned out


@dataclass
class Signal:
    code: str
    name: str  # hierarchical: tb.dut.count
    width: int
    kind: str  # wire, reg, integer, real, parameter, ...


@dataclass
class Waves:
    timescale: str = "1s"
    end: int = 0
    signals: list[Signal] = field(default_factory=list)
    times: dict[str, list[int]] = field(default_factory=dict)  # code -> change times
    values: dict[str, list[str]] = field(default_factory=dict)  # code -> values at those times


_cache: collections.OrderedDict[tuple[str, float], Waves] = collections.OrderedDict()
_lock = threading.Lock()


def load(path: Path) -> Waves:
    key = (str(path), path.stat().st_mtime)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    waves = parse(path)
    with _lock:
        _cache[key] = waves
        while len(_cache) > 4:
            _cache.popitem(last=False)
    return waves


def parse(path: Path) -> Waves:
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError(f"the waveform is larger than {MAX_BYTES // 2**20} MB; download it instead")
    w = Waves()
    scope: list[str] = []
    seen_codes: set[str] = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        # Header: scopes, variables, the timescale.
        pending: list[str] = []
        for line in fh:
            pending.extend(line.split())
            if "$end" not in pending:
                continue
            tokens, pending = pending, []
            if not tokens:
                continue
            word = tokens[0]
            if word == "$enddefinitions":
                break
            if word == "$timescale":
                w.timescale = "".join(t for t in tokens[1:] if t != "$end") or "1s"
            elif word == "$scope" and len(tokens) >= 3:
                scope.append(tokens[2])
            elif word == "$upscope":
                if scope:
                    scope.pop()
            elif word == "$var" and len(tokens) >= 5:
                kind, width, code, name = tokens[1], tokens[2], tokens[3], tokens[4]
                if len(tokens) > 6 and tokens[5].startswith("["):
                    name += tokens[5]
                full = ".".join(scope + [name])
                try:
                    bits = int(width)
                except ValueError:
                    bits = 1
                w.signals.append(Signal(code, full, bits, kind))
                if code not in seen_codes:
                    seen_codes.add(code)
                    w.times[code] = []
                    w.values[code] = []
        # Value changes.
        now = 0
        times, values = w.times, w.values
        for line in fh:
            tokens = line.split()
            i = 0
            while i < len(tokens):
                t = tokens[i]
                c = t[0]
                if c == "#":
                    try:
                        now = int(t[1:])
                    except ValueError:
                        pass
                    i += 1
                elif c in "bBrR":
                    if i + 1 < len(tokens):
                        code = tokens[i + 1]
                        if code in times:
                            _put(times[code], values[code], now, t[1:].lower() if c in "bB" else t[1:])
                    i += 2
                elif c == "$":
                    i += 1
                elif c in "01xzXZuUwW-hHlL" and len(t) > 1:
                    code = t[1:]
                    if code in times:
                        _put(times[code], values[code], now, c.lower())
                    i += 1
                else:
                    i += 1
        w.end = now
    return w


def _put(times: list[int], values: list[str], t: int, v: str) -> None:
    if times and times[-1] == t:
        values[-1] = v  # the last value at the same time wins
        return
    if values and values[-1] == v:
        return
    times.append(t)
    values.append(v)


def meta(w: Waves) -> dict:
    return {
        "timescale": w.timescale,
        "end": w.end,
        "signals": [{"code": s.code, "name": s.name, "width": s.width, "kind": s.kind} for s in w.signals],
    }


def window(w: Waves, codes: list[str], t0: int, t1: int) -> dict[str, list[list]]:
    """{code: [[time, value], ...]} between t0 and t1, starting with the value at t0; a signal
    with more changes than MAX_POINTS keeps evenly spread ones (the viewer draws it as busy)."""
    out: dict[str, list[list]] = {}
    for code in codes[:200]:
        times = w.times.get(code)
        if times is None:
            continue
        values = w.values[code]
        lo = bisect.bisect_right(times, t0) - 1
        hi = bisect.bisect_right(times, t1)
        lo = max(lo, 0)
        pts = [[times[i], values[i]] for i in range(lo, hi)]
        if len(pts) > MAX_POINTS:
            step = len(pts) / MAX_POINTS
            pts = [pts[int(i * step)] for i in range(MAX_POINTS)] + [pts[-1]]
            pts.append(["busy", 1])
        out[code] = pts
    return out


def summary(w: Waves, names: list[str] | None = None, limit: int = 40) -> list[dict]:
    """A text-friendly look at some signals (for an AI): each one's value changes, cut short."""
    out = []
    want = set(names or [])
    for s in w.signals:
        if want and s.name not in want and s.name.split(".")[-1] not in want:
            continue
        times, values = w.times.get(s.code, []), w.values.get(s.code, [])
        changes = [[t, v if s.width == 1 else _hex(v)] for t, v in zip(times[:limit], values[:limit])]
        out.append({"name": s.name, "width": s.width, "changes": changes, "more": max(0, len(times) - limit)})
        if len(out) >= 40:
            break
    return out


def _hex(bits: str) -> str:
    if any(c not in "01" for c in bits):
        return bits
    width = len(bits)
    return f"{int(bits, 2):0{max(1, (width + 3) // 4)}x}" if bits else bits
