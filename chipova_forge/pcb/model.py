"""The board as data: its parts (a footprint, the symbol's pins, the net of each pad), where they
sit, the outline, the routing rules, and the copper once routed. JSON in and out.

Units are millimetres. Axes are KiCad's: x to the right, y down, angles counter-clockwise as
seen from the top. Positions are on the board (its top-left corner is 0, 0); ``Design.origin``
is where that corner sits on KiCad's page.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any


@dataclasses.dataclass
class Pad:
    number: str
    x: float
    y: float
    w: float
    h: float
    shape: str = "roundrect"  # roundrect | rect | circle | oval
    kind: str = "smd"  # smd | thru_hole
    drill: float = 0.0
    side: str = "F"  # an SMD pad's copper layer: F (top) or B (bottom, bare copper)
    paste: bool = True
    mask: bool = True  # an opening in the solder mask (thermal vias stay tented)
    solid: bool = False  # joins copper pours fully (no thermal spokes): exposed pads and their vias


@dataclasses.dataclass
class Footprint:
    name: str
    pads: list[Pad]
    body: list[float]  # [w, h]
    courtyard: list[float]  # [x1, y1, x2, y2]
    silk: list[list[float]] = dataclasses.field(default_factory=list)  # [x1, y1, x2, y2] lines
    pin1: list[float] = dataclasses.field(default_factory=list)  # [x, y] of the pin-1 dot
    attr: str = "smd"  # smd | through_hole
    description: str = ""
    ref_layer: str = "F.SilkS"  # where the reference shows (small passives: the assembly layer)
    fab: list[float] = dataclasses.field(default_factory=list)  # [x1, y1, x2, y2] of the body; [] = centred on the origin

    def pad(self, number: str) -> Pad:
        for p in self.pads:
            if p.number == number:
                return p
        raise KeyError(f"{self.name} has no pad {number}")


@dataclasses.dataclass
class Pin:
    """A pin of the part's schematic symbol; its number is its pad's."""

    number: str
    name: str
    type: str = "passive"  # KiCad's pin types: input, output, bidirectional, power_in, passive, open_collector, no_connect
    side: str = "left"  # left | right | top | bottom


@dataclasses.dataclass
class Part:
    ref: str
    value: str
    footprint: Footprint
    pins: list[Pin]
    nets: dict[str, str]  # pad number -> net ("" = not connected)
    symbol: str = ""
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    in_bom: bool = True
    x: float = 0.0
    y: float = 0.0
    rot: int = 0
    near: str = ""  # "U1:12": placed by that pad
    group: str = ""  # connector | chip | right


@dataclasses.dataclass
class Track:
    net: str
    layer: str
    width: float
    points: list[list[float]]


@dataclasses.dataclass
class Via:
    net: str
    x: float
    y: float
    size: float
    drill: float


@dataclasses.dataclass
class Text:
    text: str
    x: float
    y: float
    size: float = 1.0
    layer: str = "F.SilkS"
    justify: str = ""  # "" (centred) | left | right


@dataclasses.dataclass
class Rules:
    clearance: float = 0.2
    track: float = 0.25
    power_track: float = 0.3
    via: float = 0.6
    via_drill: float = 0.3
    edge_clearance: float = 0.5


@dataclasses.dataclass
class Design:
    title: str
    parts: list[Part]
    power_nets: list[str]
    width: float = 0.0
    height: float = 0.0
    origin: list[float] = dataclasses.field(default_factory=lambda: [100.0, 70.0])
    rules: Rules = dataclasses.field(default_factory=Rules)
    texts: list[Text] = dataclasses.field(default_factory=list)
    tracks: list[Track] = dataclasses.field(default_factory=list)
    vias: list[Via] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)

    def part(self, ref: str) -> Part:
        for p in self.parts:
            if p.ref == ref:
                return p
        raise KeyError(f"there is no part {ref}")

    def nets(self) -> list[str]:
        """Every net, the power nets first."""
        names = {n for p in self.parts for n in p.nets.values() if n}
        return [n for n in self.power_nets if n in names] + sorted(names - set(self.power_nets))

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=1)

    @staticmethod
    def from_json(text: str) -> Design:
        return _design(json.loads(text))


def _design(d: dict[str, Any]) -> Design:
    parts = []
    for p in d["parts"]:
        fp = dict(p["footprint"])
        fp["pads"] = [Pad(**x) for x in fp["pads"]]
        parts.append(Part(**{**p, "footprint": Footprint(**fp), "pins": [Pin(**x) for x in p["pins"]]}))
    return Design(
        title=d["title"], parts=parts, power_nets=list(d["power_nets"]), width=d["width"], height=d["height"],
        origin=list(d["origin"]), rules=Rules(**d["rules"]), texts=[Text(**t) for t in d.get("texts", [])],
        tracks=[Track(**t) for t in d.get("tracks", [])], vias=[Via(**v) for v in d.get("vias", [])], notes=list(d.get("notes", [])),
    )


# ------------------------------------------------------------------ geometry


def rotate(x: float, y: float, deg: int) -> tuple[float, float]:
    """A point of a footprint turned ``deg`` (0, 90, 180, 270) counter-clockwise as seen from
    the top, in KiCad's axes (y down)."""
    deg %= 360
    if deg == 0:
        return x, y
    if deg == 90:
        return y, -x
    if deg == 180:
        return -x, -y
    if deg == 270:
        return -y, x
    raise ValueError("parts turn by 0, 90, 180 or 270 degrees")


def pad_at(part: Part, pad: Pad) -> tuple[float, float]:
    dx, dy = rotate(pad.x, pad.y, part.rot)
    return part.x + dx, part.y + dy


def pad_size(part: Part, pad: Pad) -> tuple[float, float]:
    return (pad.h, pad.w) if part.rot % 180 == 90 else (pad.w, pad.h)


def courtyard(part: Part) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = part.footprint.courtyard
    pts = [rotate(x, y, part.rot) for x, y in ((x1, y1), (x2, y2))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return part.x + min(xs), part.y + min(ys), part.x + max(xs), part.y + max(ys)


def overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float = 0.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap
