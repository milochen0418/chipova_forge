"""The Specctra design file the autorouter (Freerouting) reads: the board's outline and two copper
layers, each part as an image of its pads, the parts where placement put them, and the nets
with their widths and clearances. Freerouting doesn't move parts; it writes the copper back as
a session file (``ses``).

Specctra's axes have y up and its unit here is the micrometre (a tenth of a micrometre
resolution); Forge's y is negated on the way out and back in.
"""

from __future__ import annotations

from .model import Design, Footprint, Pad

UM = 1000.0


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def _n(v: float) -> str:
    s = f"{v * UM:.1f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def padstack_name(pad: Pad) -> str:
    w, h = round(pad.w * UM), round(pad.h * UM)
    if pad.kind == "thru_hole":
        shape = "Rect" if pad.shape == "rect" else "Round"
        return f"{shape}[A]Pad_{w}x{h}_um" if shape == "Rect" else f"Round[A]Pad_{w}_um"
    return f"Rect[{'B' if pad.side == 'B' else 'T'}]Pad_{w}x{h}_um"


def _padstack(pad: Pad) -> str:
    hw, hh = pad.w / 2, pad.h / 2
    if pad.kind == "thru_hole":
        if pad.shape == "rect":
            shapes = "".join(f"(shape (rect {lay} {_n(-hw)} {_n(-hh)} {_n(hw)} {_n(hh)}))" for lay in ("F.Cu", "B.Cu"))
        else:
            shapes = "".join(f"(shape (circle {lay} {_n(pad.w)}))" for lay in ("F.Cu", "B.Cu"))
    else:
        lay = "B.Cu" if pad.side == "B" else "F.Cu"
        shapes = f"(shape (rect {lay} {_n(-hw)} {_n(-hh)} {_n(hw)} {_n(hh)}))"
    return f"    (padstack {_q(padstack_name(pad))} {shapes} (attach off))\n"


def pin_ids(fp: Footprint) -> list[str]:
    """Each pad's name in the design file: its number, and "17@1", "17@2"... for more pads with
    the same number (the exposed pad's thermal vias), as KiCad names them."""
    seen: dict[str, int] = {}
    out = []
    for p in fp.pads:
        n = seen.get(p.number, 0)
        seen[p.number] = n + 1
        out.append(p.number if n == 0 else f"{p.number}@{n}")
    return out


def via_name(d: Design) -> str:
    return f"Via[0-1]_{round(d.rules.via * UM)}:{round(d.rules.via_drill * UM)}_um"


def _image(fp: Footprint) -> str:
    pins = "".join(f"      (pin {_q(padstack_name(p))} {_q(pid)} {_n(p.x)} {_n(-p.y)})\n" for p, pid in zip(fp.pads, pin_ids(fp)))
    return f"    (image {_q(fp.name)}\n{pins}    )\n"


def write(d: Design) -> str:
    r = d.rules
    w, h = d.width, d.height
    out = [f"(pcb {_q('board.dsn')}\n",
           "  (parser (string_quote \") (space_in_quoted_tokens on) (host_cad \"Chipova Forge\") (host_version \"1\"))\n",
           "  (resolution um 10)\n  (unit um)\n",
           "  (structure\n",
           "    (layer F.Cu (type signal) (property (index 0)))\n",
           "    (layer B.Cu (type signal) (property (index 1)))\n",
           f"    (boundary (path pcb 0 0 0 {_n(w)} 0 {_n(w)} {_n(-h)} 0 {_n(-h)} 0 0))\n",
           f"    (via {_q(via_name(d))})\n",
           f"    (rule (width {_n(r.track)}) (clearance {_n(r.clearance)}) (clearance {_n(r.clearance)} (type default_smd))"
           f" (clearance {_n(r.clearance / 4)} (type smd_smd)))\n",
           "    (control (via_at_smd off))\n",
           "  )\n",
           "  (placement\n"]
    for fp_name in sorted({p.footprint.name for p in d.parts}):
        places = "".join(f"      (place {_q(p.ref)} {_n(p.x)} {_n(-p.y)} front {p.rot})\n" for p in d.parts if p.footprint.name == fp_name)
        out.append(f"    (component {_q(fp_name)}\n{places}    )\n")
    out.append("  )\n  (library\n")
    fps: dict[str, Footprint] = {}
    for p in d.parts:
        fps.setdefault(p.footprint.name, p.footprint)
    stacks: dict[str, Pad] = {}
    for fp in fps.values():
        out.append(_image(fp))
        for pad in fp.pads:
            stacks.setdefault(padstack_name(pad), pad)
    for pad in stacks.values():
        out.append(_padstack(pad))
    out.append(f"    (padstack {_q(via_name(d))} (shape (circle F.Cu {_n(r.via)})) (shape (circle B.Cu {_n(r.via)})) (attach off))\n")
    out.append("  )\n  (network\n")
    members: dict[str, list[str]] = {}
    for p in d.parts:
        for pad, pid in zip(p.footprint.pads, pin_ids(p.footprint)):
            net = p.nets.get(pad.number, "")
            if net:
                members.setdefault(net, []).append(f"{p.ref}-{pid}")
    for net in d.nets():
        out.append(f"    (net {_q(net)} (pins {' '.join(members.get(net, []))}))\n")
    signals = [n for n in d.nets() if n not in d.power_nets]
    power = [n for n in d.nets() if n in d.power_nets]
    out.append(f"    (class signal {' '.join(_q(n) for n in signals)} (circuit (use_via {_q(via_name(d))})) "
               f"(rule (width {_n(r.track)}) (clearance {_n(r.clearance)})))\n")
    if power:
        out.append(f"    (class power {' '.join(_q(n) for n in power)} (circuit (use_via {_q(via_name(d))})) "
                   f"(rule (width {_n(r.power_track)}) (clearance {_n(r.clearance)})))\n")
    out.append("  )\n  (wiring)\n)\n")
    return "".join(out)
