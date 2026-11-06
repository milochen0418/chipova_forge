"""Placement on the module template: J1 down the left edge with its pins named beside it, the
chip to its right, every small part by the pin it serves (decoupling by the supply pin, the
oscillator by the clock pin, the LED resistor by its output, the LED past its resistor), the
sensors to the right of the chip with their own small parts. Then the outline around it all.

Small parts face the pin they serve with the pad on its net; when that spot is taken they slide
along the chip's side, then step further out. Nothing overlaps (courtyards 0.2 mm apart).
"""

from __future__ import annotations

from .model import Design, Part, Text, courtyard, overlaps, pad_at

MARGIN = 1.2
GAP = 0.2
LABEL = 0.8  # the silkscreen labels' height (mm)


def _free(part: Part, placed: list[Part], keepout: list[tuple[float, float, float, float]]) -> bool:
    box = courtyard(part)
    return not any(overlaps(box, courtyard(o), GAP) for o in placed) and not any(overlaps(box, k, GAP) for k in keepout)


def _half_along(part: Part, nx: int, ny: int) -> float:
    x1, y1, x2, y2 = courtyard(part)
    return (x2 - x1) / 2 if nx else (y2 - y1) / 2


def _near(part: Part, d: Design, placed: list[Part], keepout: list) -> None:
    ref, _, num = part.near.partition(":")
    anchor = d.part(ref)
    pad = anchor.footprint.pad(num)
    px, py = pad_at(anchor, pad)
    dx, dy = px - anchor.x, py - anchor.y
    if abs(dx) >= abs(dy):
        nx, ny = (1 if dx >= 0 else -1), 0
    else:
        nx, ny = 0, (1 if dy >= 0 else -1)
    shared = anchor.nets.get(num, "")
    own = next((n for n, net in part.nets.items() if net == shared and net), None)
    rots = (0, 180) if nx else (90, 270)
    if len(part.footprint.pads) > 2:
        rots = (0,)
    ext = (pad.w if nx else pad.h) / 2 if anchor.rot % 180 == 0 else (pad.h if nx else pad.w) / 2
    for step in range(10):
        for k in [0] + [s * i for i in range(1, 25) for s in (1, -1)]:
            t = k * 0.5
            for rot in rots:
                part.rot = rot
                base = ext + 0.4 + _half_along(part, nx, ny) + step * 0.8
                part.x = round(px + nx * base + (t if ny else 0), 3)
                part.y = round(py + ny * base + (t if nx else 0), 3)
                if own is not None and len(rots) == 2:
                    # The pad on the shared net faces the anchor.
                    ox, oy = pad_at(part, part.footprint.pad(own))
                    fx, fy = pad_at(part, next(p for p in part.footprint.pads if p.number != own))
                    if (ox - px) ** 2 + (oy - py) ** 2 > (fx - px) ** 2 + (fy - py) ** 2:
                        continue
                if _free(part, placed, keepout):
                    placed.append(part)
                    return
    raise ValueError(f"no room for {part.ref} by {part.near}")


def place(d: Design) -> None:
    placed: list[Part] = []
    j1, chip = d.part("J1"), d.part("U1")
    j1.x, j1.y, j1.rot = 0.0, 0.0, 0
    placed.append(j1)
    n = len(j1.footprint.pads)
    labels = {p.number: p.name.replace("/", " ") for p in j1.pins}
    label_w = max(len(s) for s in labels.values()) * LABEL * 0.85 + 0.6
    lx1 = 1.8 + 0.3
    keepout = [(lx1, -1.3, lx1 + label_w, (n - 1) * 2.54 + 1.3)]
    cx1, cy1, cx2, cy2 = chip.footprint.courtyard
    chip.x = round(lx1 + label_w + 5.5 - cx1, 2)
    chip.y = round((n - 1) * 2.54 / 2, 2)
    placed.append(chip)
    near = [p for p in d.parts if p.near]
    for p in [p for p in near if p.near.startswith("U1:")]:
        _near(p, d, placed, keepout)
    # The sensors: right of everything so far, one under another.
    right = max(courtyard(p)[2] for p in placed) + 4.5
    y = courtyard(chip)[1]
    for p in [p for p in d.parts if p.group == "right"]:
        x1, y1, x2, y2 = p.footprint.courtyard
        p.x, p.y, p.rot = round(right - x1, 2), round(y - y1, 2), 0
        while not _free(p, placed, keepout):
            p.y += 0.5
        placed.append(p)
        y = courtyard(p)[3] + 4.0
    done = {p.ref for p in placed}
    pending = [p for p in near if p.ref not in done]
    while pending:
        ready = [p for p in pending if p.near.split(":")[0] in done]
        if not ready:
            raise ValueError("parts placed by parts that are never placed: " + ", ".join(p.ref for p in pending))
        for p in ready:
            _near(p, d, placed, keepout)
            done.add(p.ref)
        pending = [p for p in pending if p.ref not in done]

    # The outline: everything, a margin, and a line for the title.
    boxes = [courtyard(p) for p in placed] + keepout
    x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    ox, oy = MARGIN - x1, MARGIN - y1
    for p in placed:
        p.x, p.y = round(p.x + ox, 3), round(p.y + oy, 3)
    d.width = round((x2 - x1) + 2 * MARGIN, 1)
    d.height = round((y2 - y1) + 2 * MARGIN + 2.2, 1)
    d.texts = []
    for pad in j1.footprint.pads:
        px, py = pad_at(j1, pad)
        d.texts.append(Text(labels[pad.number], round(px + 2.1, 3), round(py, 3), LABEL, "F.SilkS", "left"))
    title = f"{d.title} · Chipova Forge"
    d.width = max(d.width, round(len(title) * LABEL * 0.85 + 2 * MARGIN + 0.5, 1))
    d.texts.append(Text(title, round(MARGIN, 2), round(d.height - 1.4, 2), LABEL, "F.SilkS", "left"))
