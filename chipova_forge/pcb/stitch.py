"""Ground stitching: after routing, vias on a grid wherever one fits clear of every other
copper, so the top and bottom ground pours are one piece (no pour island hangs on a single pad,
and every return path is short)."""

from __future__ import annotations

import math

from .model import Design, Via, courtyard, pad_at, pad_size

GRID = 3.0
EDGE = 1.2


def _seg_dist(px: float, py: float, a: list[float], b: list[float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _rect_dist(px: float, py: float, cx: float, cy: float, w: float, h: float) -> float:
    dx = max(abs(px - cx) - w / 2, 0.0)
    dy = max(abs(py - cy) - h / 2, 0.0)
    return math.hypot(dx, dy)


def stitch(d: Design, net: str = "GND") -> list[Via]:
    if net not in d.nets():
        return []
    r = d.rules
    radius = r.via / 2
    keep = r.clearance + 0.45  # room for the pour around the via
    pads = []
    for p in d.parts:
        for pad in p.footprint.pads:
            x, y = pad_at(p, pad)
            w, h = pad_size(p, pad)
            pads.append((x, y, w, h))
    smd_boxes = [courtyard(p) for p in d.parts if p.footprint.attr == "smd"]
    out: list[Via] = []
    nx, ny = int((d.width - 2 * EDGE) // GRID) + 1, int((d.height - 2 * EDGE) // GRID) + 1
    x0 = (d.width - (nx - 1) * GRID) / 2
    y0 = (d.height - (ny - 1) * GRID) / 2
    for i in range(nx):
        for j in range(ny):
            x, y = round(x0 + i * GRID, 3), round(y0 + j * GRID, 3)
            if x < EDGE or y < EDGE or x > d.width - EDGE or y > d.height - EDGE:
                continue
            if any(b[0] - 0.3 <= x <= b[2] + 0.3 and b[1] - 0.3 <= y <= b[3] + 0.3 for b in smd_boxes):
                continue
            if any(_rect_dist(x, y, cx, cy, w, h) < radius + keep for cx, cy, w, h in pads):
                continue
            if any(_seg_dist(x, y, t.points[k], t.points[k + 1]) < radius + t.width / 2 + keep
                   for t in d.tracks for k in range(len(t.points) - 1)):
                continue
            if any(math.hypot(x - v.x, y - v.y) < radius + v.size / 2 + keep for v in d.vias + out):
                continue
            if any(t.layer == "F.SilkS" and abs(y - t.y) < t.size and t.x - 0.5 <= x for t in d.texts):
                continue
            out.append(Via(net, x, y, r.via, r.via_drill))
    return out
