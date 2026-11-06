"""The chip's waveform as a picture (SVG): the last recorded window of some signals, one lane
each (a line for a 1-bit signal, labelled boxes for a bus), drawn on the server from the VCD the
chip library writes (``vcd.Waves``)."""

from __future__ import annotations

from html import escape

from chipova_forge.cosim import vcd

LANE = 26
LEFT = 170
WIDTH = 900


def _segments(w: vcd.Waves, code: str, t0: int, t1: int) -> list[tuple[int, int, str]]:
    times, values = w.times.get(code, []), w.values.get(code, [])
    out: list[tuple[int, int, str]] = []
    if not times:
        return out
    import bisect  # noqa: PLC0415

    i = max(0, bisect.bisect_right(times, t0) - 1)
    while i < len(times) and times[i] <= t1:
        start = max(t0, times[i])
        end = min(t1, times[i + 1]) if i + 1 < len(times) else t1
        if end >= start:
            out.append((start, end, values[i]))
        i += 1
    return out


def _value_text(v: str) -> str:
    if v and all(ch in "01" for ch in v):
        n = int(v, 2)
        return f"{n:x}" if len(v) > 4 else str(n)
    return v


def last_window(w: vcd.Waves, span: int) -> tuple[int, int]:
    end = w.end
    return max(0, end - span), end


def render(w: vcd.Waves, names: list[str], t0: int, t1: int) -> str:
    sig_by_name = {s.name: s for s in w.signals}
    rows = [sig_by_name[n] for n in names if n in sig_by_name]
    height = 30 + LANE * len(rows) + 10
    span = max(1, t1 - t0)

    def x(t: int) -> float:
        return LEFT + (t - t0) * (WIDTH - LEFT - 10) / span

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="100%" font-family="ui-monospace,monospace" font-size="10">',
             f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="#020617"/>']
    # Time axis
    for k in range(6):
        t = t0 + span * k // 5
        parts.append(f'<line x1="{x(t):.1f}" y1="18" x2="{x(t):.1f}" y2="{height - 6}" stroke="#1e293b"/>'
                     f'<text x="{x(t):.1f}" y="12" text-anchor="middle" fill="#64748b">{_time_label(t, w.timescale)}</text>')
    for r, s in enumerate(rows):
        y = 30 + r * LANE
        parts.append(f'<text x="{LEFT - 8}" y="{y + 15}" text-anchor="end" fill="#cbd5e1">{escape(s.name.split(".")[-1] if s.name.count(".") > 2 else s.name)}</text>')
        segs = _segments(w, s.code, t0, t1)
        if s.width == 1:
            pts = []
            for a, b2, v in segs:
                level = y + 4 if v in ("1",) else (y + 20 if v in ("0",) else y + 12)
                pts.append(f"{x(a):.1f},{level}")
                pts.append(f"{x(b2):.1f},{level}")
            if pts:
                parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#34d399" stroke-width="1.3"/>')
        else:
            for a, b2, v in segs:
                xa, xb = x(a), x(b2)
                parts.append(f'<path d="M{xa + 3:.1f},{y + 4} L{xb - 3:.1f},{y + 4} L{xb:.1f},{y + 12} L{xb - 3:.1f},{y + 20} L{xa + 3:.1f},{y + 20} L{xa:.1f},{y + 12} Z" '
                             f'fill="#0f172a" stroke="#38bdf8" stroke-width="1"/>')
                if xb - xa > 28:
                    parts.append(f'<text x="{(xa + xb) / 2:.1f}" y="{y + 15.5}" text-anchor="middle" fill="#bae6fd">{escape(_value_text(v))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _time_label(t: int, timescale: str) -> str:
    """t in the VCD's time unit, shown in µs or ms."""
    unit = {"1ps": 1e-12, "1ns": 1e-9, "1us": 1e-6, "1ms": 1e-3, "1s": 1.0}.get(timescale.replace(" ", ""), 1e-12)
    sec = t * unit
    if sec >= 1e-3:
        return f"{sec * 1e3:.3f} ms"
    return f"{sec * 1e6:.1f} µs"
