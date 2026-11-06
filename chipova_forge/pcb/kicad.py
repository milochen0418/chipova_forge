"""The KiCad files: the board (.kicad_pcb), the schematic (.kicad_sch) and the project
(.kicad_pro, Forge's design rules). Written in KiCad 8's format, which KiCad 8, 9 and 10 open.

The schematic connects by labels: every pin has its net's label at its end (no wires), the
supply nets have power flags, unused pins are marked. The board has the parts where placement
put them, the routed copper, the outline, and a ground pour on both layers (KiCad fills it).
Each footprint is linked to its symbol, so KiCad's DRC can check the board against the
schematic (parity).
"""

from __future__ import annotations

import json
import uuid

from .model import Design, Footprint, Part, Pin

NS = uuid.UUID("6f1c9d52-3c55-4d3e-9a41-0f0f0f0f4f47")
LIB = "Forge"
SHEET = "board.kicad_sch"


def _u(*key: object) -> str:
    return str(uuid.uuid5(NS, "|".join(str(k) for k in key)))


def q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def f(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


# ------------------------------------------------------------------ the board

LAYERS = """	(layers
		(0 "F.Cu" signal)
		(31 "B.Cu" signal)
		(32 "B.Adhes" user "B.Adhesive")
		(33 "F.Adhes" user "F.Adhesive")
		(34 "B.Paste" user)
		(35 "F.Paste" user)
		(36 "B.SilkS" user "B.Silkscreen")
		(37 "F.SilkS" user "F.Silkscreen")
		(38 "B.Mask" user)
		(39 "F.Mask" user)
		(40 "Dwgs.User" user "User.Drawings")
		(41 "Cmts.User" user "User.Comments")
		(42 "Eco1.User" user "User.Eco1")
		(43 "Eco2.User" user "User.Eco2")
		(44 "Edge.Cuts" user)
		(45 "Margin" user)
		(46 "B.CrtYd" user "B.Courtyard")
		(47 "F.CrtYd" user "F.Courtyard")
		(48 "B.Fab" user)
		(49 "F.Fab" user)
	)
"""


def _pin_of(part: Part, number: str) -> Pin | None:
    return next((p for p in part.pins if p.number == number), None)


def _footprint(part: Part, nets: dict[str, int], ox: float, oy: float) -> str:
    fp = part.footprint
    r = part.rot
    x1, y1, x2, y2 = fp.courtyard
    out = [f'\t(footprint {q(LIB + ":" + fp.name)}\n',
           '\t\t(layer "F.Cu")\n',
           f"\t\t(uuid {q(_u('fp', part.ref))})\n",
           f"\t\t(at {f(part.x + ox)} {f(part.y + oy)} {r})\n",
           f"\t\t(descr {q(fp.description)})\n",
           f'\t\t(property "Reference" {q(part.ref)} (at 0 {f(y1 - 0.6 if fp.ref_layer == "F.SilkS" else 0)} {r}) (layer {q(fp.ref_layer)}) (uuid {q(_u("ref", part.ref))})'
           f" (effects (font (size 0.8 0.8) (thickness 0.12))))\n",
           f'\t\t(property "Value" {q(part.value)} (at 0 {f(y2 + 0.6)} {r}) (layer "F.Fab") (hide yes) (uuid {q(_u("val", part.ref))})'
           f" (effects (font (size 0.8 0.8) (thickness 0.12))))\n",
           f"\t\t(path {q('/' + _u('sym', part.ref))})\n",
           '\t\t(sheetname "Root")\n',
           f"\t\t(sheetfile {q(SHEET)})\n",
           f"\t\t(attr {fp.attr})\n"]
    for i, (a, b, c, d) in enumerate(fp.silk):
        out.append(f'\t\t(fp_line (start {f(a)} {f(b)}) (end {f(c)} {f(d)}) (stroke (width 0.12) (type solid)) (layer "F.SilkS") (uuid {q(_u("silk", part.ref, i))}))\n')
    if fp.pin1:
        px, py = fp.pin1
        out.append(f'\t\t(fp_circle (center {f(px)} {f(py)}) (end {f(px + 0.12)} {f(py)}) (stroke (width 0.24) (type solid)) (fill solid)'
                   f' (layer "F.SilkS") (uuid {q(_u("pin1", part.ref))}))\n')
    out.append(f'\t\t(fp_rect (start {f(x1)} {f(y1)}) (end {f(x2)} {f(y2)}) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd") (uuid {q(_u("crt", part.ref))}))\n')
    bw, bh = fp.body
    fx1, fy1, fx2, fy2 = fp.fab or [-bw / 2, -bh / 2, bw / 2, bh / 2]
    out.append(f'\t\t(fp_rect (start {f(fx1)} {f(fy1)}) (end {f(fx2)} {f(fy2)}) (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab") (uuid {q(_u("fab", part.ref))}))\n')
    for i, pad in enumerate(fp.pads):
        if pad.kind == "thru_hole":
            layers = '"*.Cu"' + (' "*.Mask"' if pad.mask else "")
            kind = f"thru_hole {pad.shape if pad.shape != 'roundrect' else 'circle'}"
            extra = f" (drill {f(pad.drill)}) (remove_unused_layers no)"
        else:
            side = "B" if pad.side == "B" else "F"
            layers = q(f"{side}.Cu") + (f' "{side}.Paste"' if pad.paste else "") + (f' "{side}.Mask"' if pad.mask else "")
            kind = f"smd {pad.shape}"
            extra = " (roundrect_rratio 0.25)" if pad.shape == "roundrect" else ""
        pin = _pin_of(part, pad.number)
        net = part.nets.get(pad.number, "") or (unconnected_net(part, pin) if pin else "")
        net_s = f" (net {nets[net]} {q(net)})" if net else ""
        pin_s = f" (pinfunction {q(pin.name)}) (pintype {q(pin.type)})" if pin else ""
        zone = " (zone_connect 2)" if pad.solid else ""
        out.append(f"\t\t(pad {q(pad.number)} {kind} (at {f(pad.x)} {f(pad.y)} {r}) (size {f(pad.w)} {f(pad.h)}){extra} (layers {layers})"
                   f"{net_s}{pin_s}{zone} (uuid {q(_u('pad', part.ref, i))}))\n")
    out.append("\t)\n")
    return "".join(out)


def unconnected_net(part: Part, pin: Pin) -> str:
    """The net KiCad gives a pin nothing connects to (the board's pad gets the same, for parity)."""
    return f"unconnected-({part.ref}-{pin.name}-Pad{pin.number})"


def write_pcb(d: Design) -> str:
    ox, oy = d.origin
    nets = {"": 0}
    for i, n in enumerate(d.nets(), 1):
        nets[n] = i
    for p in d.parts:
        for pin in p.pins:
            if not p.nets.get(pin.number):
                nets.setdefault(unconnected_net(p, pin), len(nets))
    out = ["(kicad_pcb\n", "\t(version 20240108)\n", '\t(generator "chipova_forge")\n', '\t(generator_version "1")\n',
           "\t(general (thickness 1.6) (legacy_teardrops no))\n", '\t(paper "A4")\n',
           f'\t(title_block (title {q(d.title)}) (company "Chipova Forge"))\n', LAYERS,
           "\t(setup (pad_to_mask_clearance 0) (allow_soldermask_bridges_in_footprints no))\n"]
    out += [f"\t(net {i} {q(n)})\n" for n, i in nets.items()]
    for p in d.parts:
        out.append(_footprint(p, nets, ox, oy))
    out.append(f'\t(gr_rect (start {f(ox)} {f(oy)}) (end {f(ox + d.width)} {f(oy + d.height)}) (stroke (width 0.05) (type default)) (fill none)'
               f' (layer "Edge.Cuts") (uuid {q(_u("edge"))}))\n')
    for i, t in enumerate(d.texts):
        just = f" (justify {t.justify})" if t.justify else ""
        out.append(f"\t(gr_text {q(t.text)} (at {f(t.x + ox)} {f(t.y + oy)} 0) (layer {q(t.layer)}) (uuid {q(_u('text', i))})"
                   f" (effects (font (size {f(t.size)} {f(t.size)}) (thickness {f(max(0.1, t.size * 0.15))})){just}))\n")
    for i, t in enumerate(d.tracks):
        for j in range(len(t.points) - 1):
            (a, b), (c, e) = t.points[j], t.points[j + 1]
            out.append(f"\t(segment (start {f(a + ox)} {f(b + oy)}) (end {f(c + ox)} {f(e + oy)}) (width {f(t.width)}) (layer {q(t.layer)})"
                       f" (net {nets.get(t.net, 0)}) (uuid {q(_u('seg', i, j))}))\n")
    for i, v in enumerate(d.vias):
        out.append(f'\t(via (at {f(v.x + ox)} {f(v.y + oy)}) (size {f(v.size)}) (drill {f(v.drill)}) (layers "F.Cu" "B.Cu") (net {nets.get(v.net, 0)})'
                   f" (uuid {q(_u('via', i))}))\n")
    gnd = "GND" if "GND" in nets else ""
    if gnd:
        inset = d.rules.edge_clearance
        pts = [(ox + inset, oy + inset), (ox + d.width - inset, oy + inset), (ox + d.width - inset, oy + d.height - inset), (ox + inset, oy + d.height - inset)]
        poly = " ".join(f"(xy {f(x)} {f(y)})" for x, y in pts)
        for layer in ("F.Cu", "B.Cu"):
            out.append(f"\t(zone (net {nets[gnd]}) (net_name {q(gnd)}) (layer {q(layer)}) (uuid {q(_u('zone', layer))}) (hatch edge 0.5)\n"
                       f"\t\t(connect_pads (clearance {f(d.rules.clearance + 0.05)}))\n"
                       "\t\t(min_thickness 0.25) (filled_areas_thickness no)\n"
                       "\t\t(fill (thermal_gap 0.3) (thermal_bridge_width 0.3))\n"
                       f"\t\t(polygon (pts {poly}))\n\t)\n")
    out.append(")\n")
    return "".join(out)


# ------------------------------------------------------------------ the schematic

G = 2.54


def _snap(v: float, step: float = 1.27) -> float:
    return round(round(v / step) * step, 4)


CHAR = 0.85  # a 1.27 mm character's width


def _up(v: float, step: float = G) -> float:
    import math  # noqa: PLC0415

    return round(math.ceil(v / step - 1e-9) * step, 4)


TWO_PIN = {
    # name: (graphics, pin length, the pin on top)
    "R": ("(rectangle (start -1.016 2.54) (end 1.016 -2.54) (stroke (width 0.254) (type default)) (fill (type none)))", 1.27, "1"),
    "C": ("(polyline (pts (xy -2.032 0.762) (xy 2.032 0.762)) (stroke (width 0.508) (type default)) (fill (type none)))"
          " (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762)) (stroke (width 0.508) (type default)) (fill (type none)))", 3.048, "1"),
    "LED": ("(polyline (pts (xy -1.27 1.27) (xy 1.27 1.27) (xy 0 -1.27) (xy -1.27 1.27)) (stroke (width 0.254) (type default)) (fill (type none)))"
            " (polyline (pts (xy -1.27 -1.27) (xy 1.27 -1.27)) (stroke (width 0.254) (type default)) (fill (type none)))", 2.54, "2"),
    "D": ("(polyline (pts (xy -1.27 1.27) (xy 1.27 1.27) (xy 0 -1.27) (xy -1.27 1.27)) (stroke (width 0.254) (type default)) (fill (type outline)))"
          " (polyline (pts (xy -1.27 -1.27) (xy 1.27 -1.27)) (stroke (width 0.254) (type default)) (fill (type none)))", 2.54, "2"),
}


class Symbol:
    """A part's schematic symbol: a resistor, capacitor or LED drawn as such (pins top and
    bottom), anything else a box with its pins on the sides system.yaml and the chip's pin
    list give, the names inside and sized so they never overlap."""

    def __init__(self, name: str, pins: list[Pin]) -> None:
        self.name = name
        self.pins = pins
        self.at: dict[str, tuple[float, float, int, str]] = {}  # number -> x, y, angle, side
        self.two_pin = name in TWO_PIN and len(pins) == 2
        if self.two_pin:
            top = TWO_PIN[name][2]
            for p in pins:
                self.at[p.number] = (0.0, 3.81, 270, "top") if p.number == top else (0.0, -3.81, 90, "bottom")
            self.half_w, self.half_h = 1.27, 3.81
            return
        side = {s: [p for p in pins if p.side == s] for s in ("left", "right", "top", "bottom")}
        rows = max(len(side["left"]), len(side["right"]), 1)
        cols = max(len(side["top"]), len(side["bottom"]), 0)
        lr = max((len(p.name) for p in side["left"]), default=0), max((len(p.name) for p in side["right"]), default=0)
        tb = max((len(p.name) for p in side["top"] + side["bottom"]), default=0)
        half_w = _up(max(2 * G, cols * G / 2 + max(lr) * CHAR + 2.0, (lr[0] + lr[1]) * CHAR / 2 + 2.0))
        half_h = _up(max(2 * G, rows * G / 2 + 1.27, (tb * CHAR + 1.5) if cols else 0))
        self.half_w, self.half_h = half_w, half_h
        for s, plist in side.items():
            n = len(plist)
            for i, p in enumerate(plist):
                off = _snap((i - (n - 1) / 2) * G)
                if s == "left":
                    self.at[p.number] = (-half_w - G, -off, 0, s)
                elif s == "right":
                    self.at[p.number] = (half_w + G, -off, 180, s)
                elif s == "top":
                    self.at[p.number] = (off, half_h + G, 270, s)
                else:
                    self.at[p.number] = (off, -half_h - G, 90, s)

    def lib(self) -> str:
        n = self.name
        hide = "(effects (font (size 1.27 1.27)) hide)"
        out = [f"\t\t(symbol {q(LIB + ':' + n)}" + (" (pin_numbers hide) (pin_names (offset 0) hide)" if self.two_pin else " (pin_names (offset 0.508))")
               + " (exclude_from_sim no) (in_bom yes) (on_board yes)\n",
               f'\t\t\t(property "Reference" "U" (at 0 {f(self.half_h + 1.27)} 0) (effects (font (size 1.27 1.27))))\n',
               f"\t\t\t(property \"Value\" {q(n)} (at 0 {f(-self.half_h - 1.27)} 0) (effects (font (size 1.27 1.27))))\n",
               f'\t\t\t(property "Footprint" "" (at 0 0 0) {hide})\n',
               f'\t\t\t(property "Datasheet" "" (at 0 0 0) {hide})\n']
        if self.two_pin:
            graphics, length, _ = TWO_PIN[n]
            out.append(f"\t\t\t(symbol {q(n + '_0_1')} {graphics})\n")
        else:
            length = G
            out.append(f"\t\t\t(symbol {q(n + '_0_1')} (rectangle (start {f(-self.half_w)} {f(self.half_h)}) (end {f(self.half_w)} {f(-self.half_h)})"
                       " (stroke (width 0.254) (type default)) (fill (type background))))\n")
        out.append(f"\t\t\t(symbol {q(n + '_1_1')}\n")
        for p in self.pins:
            x, y, a, _ = self.at[p.number]
            out.append(f"\t\t\t\t(pin {p.type} line (at {f(x)} {f(y)} {a}) (length {f(length)}) (name {q(p.name)} (effects (font (size 1.27 1.27))))"
                       f" (number {q(p.number)} (effects (font (size 1.27 1.27)))))\n")
        out.append("\t\t\t)\n\t\t)\n")
        return "".join(out)


PWR_FLAG = """		(symbol "power:PWR_FLAG" (power) (pin_numbers hide) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)
			(property "Reference" "#FLG" (at 0 1.905 0) (effects (font (size 1.27 1.27)) hide))
			(property "Value" "PWR_FLAG" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
			(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
			(property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
			(symbol "PWR_FLAG_0_0" (pin power_out line (at 0 0 90) (length 0) (name "pwr" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27))))))
			(symbol "PWR_FLAG_0_1" (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27)) (stroke (width 0) (type default)) (fill (type none))))
		)
"""


def _label(net: str, x: float, y: float, side: str, key: str) -> str:
    """A global label at a pin's end (a local label would name the net "/NAME", not the
    board's "NAME")."""
    angle, just = {"left": (180, "right"), "right": (0, "left"), "top": (90, "left"), "bottom": (270, "right")}[side]
    return (f"\t(global_label {q(net)} (shape passive) (at {f(x)} {f(y)} {angle}) (fields_autoplaced yes)"
            f" (effects (font (size 1.27 1.27)) (justify {just})) (uuid {q(_u('label', key))})"
            f' (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {f(x)} {f(y)} 0) (effects (font (size 1.27 1.27)) hide)))\n')


def write_sch(d: Design, project: str = "board") -> str:
    root = _u("root")
    symbols: dict[str, Symbol] = {}
    for p in d.parts:
        symbols.setdefault(p.symbol or p.ref, Symbol(p.symbol or p.ref, p.pins))
    out = ["(kicad_sch\n", "\t(version 20231120)\n", '\t(generator "chipova_forge")\n', '\t(generator_version "1")\n',
           f"\t(uuid {q(root)})\n", '\t(paper "A3")\n', f'\t(title_block (title {q(d.title)}) (company "Chipova Forge"))\n',
           "\t(lib_symbols\n"] + [s.lib() for s in symbols.values()] + [PWR_FLAG, "\t)\n"]
    # Parts in rows, each cell wide enough for its labels.
    x, y, row_h = 30.48, 40.64, 0.0
    hide = "(effects (font (size 1.27 1.27)) hide)"
    for p in sorted(d.parts, key=lambda p: (p.ref != "U1", p.ref[0], int("".join(c for c in p.ref if c.isdigit()) or 0))):
        s = symbols[p.symbol or p.ref]
        longest = max((len(n) for n in p.nets.values()), default=4) * CHAR + 4
        w = 2 * (s.half_w + G) + 2 * longest
        h = 2 * (s.half_h + G) + 2 * longest * (1 if any(pin.side in ("top", "bottom") for pin in p.pins) else 0) + 6
        if x + w > 400:
            x, y, row_h = 30.48, y + row_h, 0.0
        cx, cy = _snap(x + w / 2, G), _snap(y + h / 2, G)
        row_h = max(row_h, h)
        if s.two_pin:
            rx, ry, vx, vy = cx + 2.54, cy - 0.635, cx + 2.54, cy + 1.905
        else:
            rx, ry, vx, vy = cx - s.half_w, cy - s.half_h - 1.27, cx - s.half_w, cy + s.half_h + 2.54
        x += w + 5.08
        out.append(f"\t(symbol (lib_id {q(LIB + ':' + (p.symbol or p.ref))}) (at {f(cx)} {f(cy)} 0) (unit 1) (exclude_from_sim no)"
                   f" (in_bom {'yes' if p.in_bom else 'no'}) (on_board yes) (dnp no) (uuid {q(_u('sym', p.ref))})\n"
                   f"\t\t(property \"Reference\" {q(p.ref)} (at {f(rx)} {f(ry)} 0) (effects (font (size 1.27 1.27)) (justify left)))\n"
                   f"\t\t(property \"Value\" {q(p.value)} (at {f(vx)} {f(vy)} 0) (effects (font (size 1.27 1.27)) (justify left)))\n"
                   f"\t\t(property \"Footprint\" {q(LIB + ':' + p.footprint.name)} (at {f(cx)} {f(cy)} 0) {hide})\n"
                   f"\t\t(property \"Datasheet\" \"\" (at {f(cx)} {f(cy)} 0) {hide})\n"
                   f"\t\t(property \"Description\" {q(p.description)} (at {f(cx)} {f(cy)} 0) {hide})\n"
                   f"\t\t(property \"MPN\" {q(p.mpn)} (at {f(cx)} {f(cy)} 0) {hide})\n"
                   f"\t\t(property \"Manufacturer\" {q(p.manufacturer)} (at {f(cx)} {f(cy)} 0) {hide})\n")
        for pin in p.pins:
            out.append(f"\t\t(pin {q(pin.number)} (uuid {q(_u('pin', p.ref, pin.number))}))\n")
        out.append(f"\t\t(instances (project {q(project)} (path {q('/' + root)} (reference {q(p.ref)}) (unit 1))))\n\t)\n")
        for pin in p.pins:
            px, py, _, side = s.at[pin.number]
            ax, ay = _snap(cx + px), _snap(cy - py)
            net = p.nets.get(pin.number, "")
            if net:
                out.append(_label(net, ax, ay, side, f"{p.ref}.{pin.number}"))
            elif pin.type != "no_connect":
                out.append(f"\t(no_connect (at {f(ax)} {f(ay)}) (uuid {q(_u('nc', p.ref, pin.number))}))\n")
    # A power flag on each supply net: the module's supply comes in on J1.
    for i, net in enumerate(n for n in d.power_nets if n in d.nets()):
        fx, fy = _snap(30.48 + i * 20.32, G), 20.32
        out.append(f"\t(symbol (lib_id \"power:PWR_FLAG\") (at {f(fx)} {f(fy)} 0) (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)"
                   f" (uuid {q(_u('flag', net))})\n"
                   f"\t\t(property \"Reference\" {q(f'#FLG0{i + 1}')} (at {f(fx)} {f(fy - 1.905)} 0) {hide})\n"
                   f"\t\t(property \"Value\" \"PWR_FLAG\" (at {f(fx)} {f(fy - 3.81)} 0) (effects (font (size 1.27 1.27))))\n"
                   f"\t\t(property \"Footprint\" \"\" (at {f(fx)} {f(fy)} 0) {hide})\n"
                   f"\t\t(property \"Datasheet\" \"~\" (at {f(fx)} {f(fy)} 0) {hide})\n"
                   f"\t\t(pin \"1\" (uuid {q(_u('flagpin', net))}))\n"
                   f"\t\t(instances (project {q(project)} (path {q('/' + root)} (reference {q(f'#FLG0{i + 1}')}) (unit 1))))\n\t)\n")
        out.append(_label(net, fx, fy, "bottom", f"flag.{net}"))
    out.append('\t(sheet_instances (path "/" (page "1")))\n)\n')
    return "".join(out)


# ------------------------------------------------------------------ the project


def write_pro(d: Design, project: str = "board") -> str:
    r = d.rules
    cls = {"bus_width": 12, "clearance": r.clearance, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2,
           "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
           "schematic_color": "rgba(0, 0, 0, 0.000)", "via_diameter": r.via, "via_drill": r.via_drill, "wire_width": 6}
    pro = {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.05, "copper_line_width": 0.2, "silk_line_width": 0.12, "silk_text_size_h": 0.8,
                         "silk_text_size_v": 0.8, "silk_text_thickness": 0.12},
            "rule_severities": {"lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore", "silk_overlap": "warning",
                                "silk_over_copper": "warning", "silk_edge_clearance": "warning", "text_height": "warning",
                                "text_thickness": "warning", "footprint_type_mismatch": "ignore"},
            "rules": {"min_clearance": r.clearance - 0.05, "min_copper_edge_clearance": 0.3, "min_hole_clearance": 0.25,
                      "min_hole_to_hole": 0.25, "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1, "min_silk_clearance": 0.0,
                      "min_text_height": 0.6, "min_text_thickness": 0.08, "min_through_hole_diameter": 0.3, "min_track_width": 0.15,
                      "min_via_annular_width": 0.1, "min_via_diameter": 0.5, "solder_mask_to_copper_clearance": 0.0},
            "track_widths": [0.0, r.track, r.power_track],
            "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": r.via, "drill": r.via_drill}],
        }},
        "erc": {"rule_severities": {"lib_symbol_issues": "ignore", "lib_symbol_mismatch": "ignore", "footprint_link_issues": "ignore",
                                    "simulation_model_issue": "ignore"}},
        "meta": {"filename": f"{project}.kicad_pro", "version": 1},
        "net_settings": {
            "classes": [{**cls, "name": "Default", "priority": 2147483647, "track_width": r.track},
                        {**cls, "name": "Power", "priority": 0, "track_width": r.power_track}],
            "meta": {"version": 3},
            "netclass_patterns": [{"netclass": "Power", "pattern": n} for n in d.power_nets],
        },
        "sheets": [[_u("root"), "Root"]],
        "text_variables": {},
    }
    return json.dumps(pro, indent=2) + "\n"
