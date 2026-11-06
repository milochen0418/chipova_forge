"""The analog side as a picture (SVG): an output over the last seconds of virtual time, with the
times a drive (the fan) was on shaded, and lines at given levels (the chip's threshold)."""

from __future__ import annotations

from html import escape

W, H, LEFT, BOTTOM, TOP = 1100, 230, 48, 22, 10


def render(history: list[tuple[float, dict, dict]], output: str, unit: str, drive: str = "", window: float = 30.0,
           marks: dict[str, float] | None = None) -> str:
    pts = [(t, v[output]) for t, v, _ in history if output in v]
    if len(pts) < 2:
        return ""
    t1 = pts[-1][0]
    t0 = max(pts[0][0], t1 - window)
    pts = [(t, y) for t, y in pts if t >= t0]
    ys = [y for _, y in pts] + list((marks or {}).values())
    lo, hi = min(ys), max(ys)
    pad = max(0.2, (hi - lo) * 0.15)
    lo, hi = lo - pad, hi + pad
    span = max(t1 - t0, 1e-6)
    pw, ph = W - LEFT - 8, H - BOTTOM - TOP

    def x(t: float) -> float:
        return LEFT + (t - t0) / span * pw

    def y(v: float) -> float:
        return TOP + (hi - v) / (hi - lo) * ph

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" font-family="ui-monospace,monospace" font-size="10">',
           f'<rect x="{LEFT}" y="{TOP}" width="{pw}" height="{ph}" fill="#0b1020" stroke="#1e293b"/>']
    if drive:
        on_since = None
        for t, _, d in history:
            if t < t0:
                on_since = t0 if d.get(drive) else None
                continue
            if d.get(drive) and on_since is None:
                on_since = t
            elif not d.get(drive) and on_since is not None:
                out.append(f'<rect x="{x(on_since):.1f}" y="{TOP}" width="{max(1.0, x(t) - x(on_since)):.1f}" height="{ph}" fill="#38bdf8" opacity="0.13"/>')
                on_since = None
        if on_since is not None:
            out.append(f'<rect x="{x(on_since):.1f}" y="{TOP}" width="{max(1.0, x(t1) - x(on_since)):.1f}" height="{ph}" fill="#38bdf8" opacity="0.13"/>')
    for label, v in (marks or {}).items():
        out.append(f'<line x1="{LEFT}" x2="{LEFT + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#f59e0b" stroke-dasharray="4 3" stroke-width="1"/>'
                   f'<text x="{LEFT + pw - 4}" y="{y(v) - 3:.1f}" text-anchor="end" fill="#fbbf24">{escape(label)} {v:g}</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(t):.1f},{y(v):.1f}" for i, (t, v) in enumerate(pts))
    out.append(f'<path d="{path}" fill="none" stroke="#f472b6" stroke-width="1.8"/>')
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        out.append(f'<text x="{LEFT - 4}" y="{y(v) + 3:.1f}" text-anchor="end" fill="#94a3b8">{v:.1f}</text>')
    out.append(f'<text x="{LEFT}" y="{H - 6}" fill="#94a3b8">t = {t0:.1f} s</text>'
               f'<text x="{LEFT + pw}" y="{H - 6}" text-anchor="end" fill="#94a3b8">{t1:.1f} s (virtual)</text>'
               f'<text x="{LEFT + pw / 2}" y="{H - 6}" text-anchor="middle" fill="#64748b">{escape(output)} ({escape(unit)})'
               + (f" · shaded: {escape(drive)} on" if drive else "") + "</text>")
    out.append("</svg>")
    return "".join(out)
