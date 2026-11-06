"""Land patterns, made from a package's numbers the way IPC-7351 nominal patterns are: pads
reach 0.4 mm past the body's leads (toe) and 0.45 mm under it (heel) for no-lead packages, the
courtyard is 0.25 mm around everything. Names follow KiCad's library names, so a person
recognises them (``QFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm``); the pads are Forge's.
"""

from __future__ import annotations

from .model import Footprint, Pad

COURTYARD = 0.25
SILK = 0.12


def _box(pads: list[Pad], body: tuple[float, float]) -> list[float]:
    xs = [p.x - p.w / 2 for p in pads] + [p.x + p.w / 2 for p in pads] + [-body[0] / 2, body[0] / 2]
    ys = [p.y - p.h / 2 for p in pads] + [p.y + p.h / 2 for p in pads] + [-body[1] / 2, body[1] / 2]
    r = lambda v: round(v, 3)  # noqa: E731
    return [r(min(xs) - COURTYARD), r(min(ys) - COURTYARD), r(max(xs) + COURTYARD), r(max(ys) + COURTYARD)]


def _fmt(v: float) -> str:
    return f"{v:g}"


def qfn(pins: int, body_w: float, body_h: float, pitch: float, ep: tuple[float, float] | None = None) -> Footprint:
    """A quad no-lead package: ``pins`` around four sides, numbered counter-clockwise from the
    top of the left side (seen from the top), and the exposed pad (number ``pins + 1``)."""
    if pins % 4:
        raise ValueError("a QFN has the same number of pins on each side")
    k = pins // 4
    length, width = 0.85, round(pitch * 0.5, 3)
    cx, cy = round(body_w / 2 - 0.025, 4), round(body_h / 2 - 0.025, 4)
    pads: list[Pad] = []
    along = [round((i - (k - 1) / 2) * pitch, 4) for i in range(k)]
    for i, a in enumerate(along):  # left, top to bottom
        pads.append(Pad(str(1 + i), -cx, a, length, width))
    for i, a in enumerate(along):  # bottom, left to right
        pads.append(Pad(str(k + 1 + i), a, cy, width, length))
    for i, a in enumerate(along):  # right, bottom to top
        pads.append(Pad(str(2 * k + 1 + i), cx, -a, length, width))
    for i, a in enumerate(along):  # top, right to left
        pads.append(Pad(str(3 * k + 1 + i), -a, -cy, width, length))
    name = f"QFN-{pins}"
    if ep:
        num = str(pins + 1)
        pads.append(Pad(num, 0.0, 0.0, ep[0], ep[1], "rect", solid=True))
        # Thermal vias through the exposed pad, and the same pad on the bottom: the pad's ground
        # (and heat) reaches the bottom layer, where tracks and the ground pour can take it.
        k_v = 2 if min(ep) < 2.6 else 3
        step = round(min(ep) / (k_v + 0.5), 2)
        for i in range(k_v):
            for j in range(k_v):
                pads.append(Pad(num, round((i - (k_v - 1) / 2) * step, 3), round((j - (k_v - 1) / 2) * step, 3), 0.6, 0.6, "circle", "thru_hole", 0.3,
                                    paste=False, mask=False, solid=True))
        pads.append(Pad(num, 0.0, 0.0, ep[0], ep[1], "rect", side="B", paste=False, mask=False, solid=True))
        name += "-1EP"
    name += f"_{_fmt(body_w)}x{_fmt(body_h)}mm_P{_fmt(pitch)}mm" + (f"_EP{_fmt(ep[0])}x{_fmt(ep[1])}mm_ThermalVias" if ep else "")
    # Silk: the body's corners, clear of the pads, and a dot by pin 1.
    hw, hh = body_w / 2 + 0.1, body_h / 2 + 0.1
    first = along[0] - width / 2 - 0.25
    t = round(abs(first) - 0.05, 3) if abs(first) < hw else 0.0
    silk = []
    if hw - t > 0.2:
        for sx in (-1, 1):
            for sy in (-1, 1):
                if sx == -1 and sy == -1:
                    continue  # pin 1's corner has the dot
                silk.append([round(sx * hw, 3), round(sy * hh, 3), round(sx * t, 3), round(sy * hh, 3)])
                silk.append([round(sx * hw, 3), round(sy * hh, 3), round(sx * hw, 3), round(sy * t, 3)])
    return Footprint(name, pads, [body_w, body_h], _box(pads, (body_w, body_h)), silk,
                     [round(-cx - length / 2 - 0.3, 3), round(along[0] - pitch, 3)],
                     description=f"QFN, {pins} pins, {body_w}x{body_h} mm body, {pitch} mm pitch")


def soic(pins: int = 8, body_w: float = 3.9, body_h: float = 4.9, pitch: float = 1.27) -> Footprint:
    """A small-outline package: pins down the left side (1 at the top), back up the right."""
    k = pins // 2
    along = [round((i - (k - 1) / 2) * pitch, 4) for i in range(k)]
    pads = [Pad(str(1 + i), -2.475, a, 1.95, 0.6) for i, a in enumerate(along)]
    pads += [Pad(str(k + 1 + i), 2.475, -a, 1.95, 0.6) for i, a in enumerate(along)]
    hw, hh = body_w / 2, body_h / 2 + 0.11
    silk = [[-hw, -hh, hw, -hh], [-hw, hh, hw, hh]]
    return Footprint(f"SOIC-{pins}_{_fmt(body_w)}x{_fmt(body_h)}mm_P{_fmt(pitch)}mm", pads, [body_w, body_h],
                     _box(pads, (body_w, body_h)), silk, [-3.45, along[0] - 0.9],
                     description=f"SOIC, {pins} pins, {body_w}x{body_h} mm body, {pitch} mm pitch")


def chip0603(kind: str = "R") -> Footprint:
    """A 0603 (1608 metric) resistor or capacitor."""
    pads = [Pad("1", -0.825, 0.0, 0.8, 0.95), Pad("2", 0.825, 0.0, 0.8, 0.95)]
    silk = [[-0.237, -0.5225, 0.237, -0.5225], [-0.237, 0.5225, 0.237, 0.5225]]
    name = {"R": "R_0603_1608Metric", "C": "C_0603_1608Metric"}[kind]
    return Footprint(name, pads, [1.6, 0.8], [-1.48, -0.73, 1.48, 0.73], silk,
                     description=f"{'Resistor' if kind == 'R' else 'Capacitor'}, 0603 (1608 metric)", ref_layer="F.Fab")


def led0603() -> Footprint:
    """A 0603 LED; pad 1 is the cathode."""
    pads = [Pad("1", -0.7875, 0.0, 0.875, 0.95), Pad("2", 0.7875, 0.0, 0.875, 0.95)]
    silk = [[0.8, -0.735, -1.485, -0.735], [-1.485, -0.735, -1.485, 0.735], [-1.485, 0.735, 0.8, 0.735]]
    return Footprint("LED_0603_1608Metric", pads, [1.6, 0.8], [-1.75, -0.98, 1.75, 0.98], silk,
                     description="LED, 0603 (1608 metric), pad 1 the cathode", ref_layer="F.Fab")


def oscillator3225() -> Footprint:
    """A 3.2 x 2.5 mm four-pad clock oscillator: 1 enable, 2 ground, 3 output, 4 supply
    (counter-clockwise from the bottom left, seen from the top). Check the pads against the
    oscillator's own datasheet."""
    pads = [Pad("1", -1.1, 0.8, 1.2, 1.0, "rect"), Pad("2", 1.1, 0.8, 1.2, 1.0, "rect"),
            Pad("3", 1.1, -0.8, 1.2, 1.0, "rect"), Pad("4", -1.1, -0.8, 1.2, 1.0, "rect")]
    silk = [[-1.8, -0.15, -1.8, 0.15], [1.8, -0.15, 1.8, 0.15]]
    return Footprint("Oscillator_SMD_3.2x2.5mm_4Pad", pads, [3.2, 2.5], [-1.95, -1.55, 1.95, 1.55], silk, [-2.15, 1.45],
                     description="Clock oscillator, 3.2x2.5 mm, 4 pads")


def header1x(n: int) -> Footprint:
    """A single-row 2.54 mm pin header, pin 1 at the origin and the rest below it."""
    pads = [Pad(str(i + 1), 0.0, round(i * 2.54, 3), 1.7, 1.7, "rect" if i == 0 else "oval", "thru_hole", 1.0) for i in range(n)]
    end = (n - 1) * 2.54
    silk = [[-1.33, 1.27, -1.33, round(end + 1.33, 3)], [1.33, 1.27, 1.33, round(end + 1.33, 3)],
            [-1.33, round(end + 1.33, 3), 1.33, round(end + 1.33, 3)], [-1.33, 1.27, 1.33, 1.27],
            [-1.33, 0.0, -1.33, -1.33], [-1.33, -1.33, 0.0, -1.33]]
    return Footprint(f"PinHeader_1x{n:02d}_P2.54mm_Vertical", pads, [2.54, round(end + 2.54, 3)],
                     [-1.8, -1.8, 1.8, round(end + 1.8, 3)], silk, attr="through_hole",
                     description=f"Pin header, 1x{n}, 2.54 mm pitch", fab=[-1.27, -1.27, 1.27, round(end + 1.27, 3)])


def sot23() -> Footprint:
    """SOT-23 (three pins: 1 and 2 on the left, 3 on the right)."""
    pads = [Pad("1", -0.9375, -0.95, 1.475, 0.6), Pad("2", -0.9375, 0.95, 1.475, 0.6), Pad("3", 0.9375, 0.0, 1.475, 0.6)]
    silk = [[0.76, -1.56, 0.76, -0.65], [0.76, 0.65, 0.76, 1.56], [-0.76, 1.56, 0.76, 1.56], [-0.76, -1.56, 0.76, -1.56]]
    return Footprint("SOT-23", pads, [1.3, 2.9], [-1.92, -1.7, 1.92, 1.7], silk, [-1.9, -1.55], description="SOT-23, 3 pins")


def sod123() -> Footprint:
    """SOD-123 diode; pad 1 is the cathode."""
    pads = [Pad("1", -1.65, 0.0, 0.9, 1.2), Pad("2", 1.65, 0.0, 0.9, 1.2)]
    silk = [[-2.36, -1.0, 1.65, -1.0], [-2.36, -1.0, -2.36, 1.0], [-2.36, 1.0, 1.65, 1.0]]
    return Footprint("D_SOD-123", pads, [2.7, 1.6], [-2.6, -1.25, 2.6, 1.25], silk, description="Diode, SOD-123, pad 1 the cathode",
                     ref_layer="F.Fab")
