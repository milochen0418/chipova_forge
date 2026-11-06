"""The carrier module's parts and nets, from the project's own files.

The module is the board Forge lays out: the chip with its decoupling, its clock (an oscillator)
and its power-on reset; each sensor of system.yaml with its decoupling and its bus's pull-ups;
an LED (and its resistor) on each chip output system.yaml puts on an LED; and one pin header,
J1, that carries the supply and every signal the MCU board (system.yaml's platform) drives or
reads. Its silkscreen names the MCU pin each J1 pin goes to.

What it doesn't do (the issues say so): chips that sit on the MCU's parallel bus (APB3 over the
FSMC: dozens of wires to a 100-pin MCU), packages other than QFN, parts without a land pattern.
"""

from __future__ import annotations

import re
from typing import Any

from chipova_forge.contract.contract import Contract
from chipova_forge.system import board as board_mod
from chipova_forge.system import catalog

from . import footprints as fps
from .model import Design, Part, Pin

GND, VCC = "GND", "+3V3"
# Real parts for the passives (the BOM's MPN column); any equivalent fits the land pattern.
R_MPN = {"330": "RC0603FR-07330RL", "4.7k": "RC0603FR-074K7L", "10k": "RC0603FR-0710KL", "100": "RC0603FR-07100RL",
         "100k": "RC0603FR-07100KL"}
C_MPN = {"100n": "GRM188R71H104KA93D", "1u": "GRM188R61E105KA12D", "10u": "GRM188R61A106KE69D"}
OSC_MPN = {25_000_000: ("ECS Inc.", "ECS-3225MV-250-BN-TR")}


def _r(ref: str, value: str, a: str, b: str, near: str) -> Part:
    return Part(ref, value, fps.chip0603("R"), [Pin("1", "~", "passive", "left"), Pin("2", "~", "passive", "right")],
                {"1": a, "2": b}, symbol="R", mpn=R_MPN.get(value, ""), manufacturer="Yageo" if value in R_MPN else "",
                description=f"Resistor {value}, 1%, 0603", near=near)


def _c(ref: str, value: str, a: str, b: str, near: str) -> Part:
    return Part(ref, value, fps.chip0603("C"), [Pin("1", "~", "passive", "top"), Pin("2", "~", "passive", "bottom")],
                {"1": a, "2": b}, symbol="C", mpn=C_MPN.get(value, ""), manufacturer="Murata" if value in C_MPN else "",
                description=f"Capacitor {value}, X7R/X5R, 0603", near=near)


def _qfn(c: Contract) -> tuple[Any, list[tuple[str, str]]]:
    pk = c.package
    assert pk is not None
    m = re.match(r"^(?:V|W)?QFN-?(\d+)$", pk.type.strip().upper())
    if not m:
        return None, [("error", f"Forge lays out QFN chips in this version; the chip's package is {pk.type or 'not given'}")]
    n = int(m.group(1))
    if n % 4 or n != len(pk.pinmap) or not pk.body_mm or not pk.pitch_mm:
        return None, [("error", "package/package.toml and pins.csv don't describe a whole QFN (pins, body_mm, pitch_mm)")]
    w, h = float(pk.body_mm[0]), float(pk.body_mm[-1])
    ep = None
    issues: list[tuple[str, str]] = []
    if pk.exposed_pad:
        size = round(min(w, h) * 0.53, 1)
        ep = (size, size)
        issues.append(("info", f"the exposed pad is {size} x {size} mm (package.toml gives no size: check the package drawing)"))
    return fps.qfn(n, w, h, float(pk.pitch_mm), ep), issues


def pin_of(c: Contract, signal: str) -> str:
    """The package pin of one of the chip's signals."""
    assert c.package is not None
    return next((str(e.pin) for e in c.package.pinmap if e.signal == signal), "")


def build(c: Contract, b: board_mod.Board, title: str) -> tuple[Design | None, list[tuple[str, str]]]:
    """The module's parts and nets, or None and why not."""
    issues: list[tuple[str, str]] = []
    if c.package is None or not c.package.pinmap:
        return None, [("error", "the chip has no package plan (package/package.toml and package/pins.csv)")]
    if c.interface.protocol == "apb3":
        return None, [("error", "an APB3 chip sits on the MCU's parallel bus (the FSMC: dozens of wires to a 100-pin MCU); "
                                "Forge lays out SPI chips and chips with no bus in this version")]
    fp, more = _qfn(c)
    issues += more
    if fp is None:
        return None, issues
    plat = catalog.platform(b.platform)
    if abs(c.electrical.vdd - 3.3) > 0.31:
        issues.append(("warning", f"the chip's supply is {c.electrical.vdd} V; the module carries 3.3 V from the MCU board"))

    roles = {p.port: p.role for p in c.pins}
    iface = {port: role for role, port in c.interface.ports.items()}
    spi_net = {"sclk": "SPI_SCK", "mosi": "SPI_MOSI", "miso": "SPI_MISO", "cs_n": "SPI_CS_N"}
    led_pins = set(b.chip.leds)
    parts: list[Part] = []
    pins: list[Pin] = []
    nets: dict[str, str] = {}
    to_mcu: list[tuple[str, str]] = []  # (net, the MCU pin) on J1
    vdd_pads: list[str] = []
    clk_pad = rst_pad = ""
    led_pads: dict[str, str] = {}
    for e in c.package.pinmap:
        num, sig, kind = str(e.pin), e.signal, e.kind
        if kind == "power":
            nets[num] = VCC
            vdd_pads.append(num)
            pins.append(Pin(num, e.pad or "VDD", "power_in", "top"))
        elif kind == "ground":
            nets[num] = GND
            pins.append(Pin(num, e.pad or "VSS", "power_in", "bottom"))
        elif kind == "nc" or not sig:
            nets[num] = ""
            pins.append(Pin(num, "NC", "no_connect", "right"))
        else:
            role = roles.get(sig, "")
            ptype = {"input": "input", "output": "output", "inout": "bidirectional"}.get(kind, "passive")
            side = "left" if ptype == "input" else "right"
            if sig in iface and c.interface.protocol == "spi":
                net = spi_net[iface[sig]]
                spi = plat["spi"][b.chip.interface]
                mcu = {"sclk": spi["sck"], "mosi": spi["mosi"], "miso": spi["miso"], "cs_n": b.chip.chip_select}[iface[sig]]
                to_mcu.append((net, mcu))
            elif role == "clock" or sig == c.clock.port:
                net, clk_pad = "CLK", num
            elif role == "reset" or sig == c.reset.port:
                net, rst_pad = sig.upper(), num
            elif sig in b.chip.irq:
                net = "IRQ" if len(b.chip.irq) == 1 else f"IRQ_{sig.upper()}"
                to_mcu.append((net, b.chip.irq[sig]))
            elif sig in led_pins:
                net = sig.upper()
                led_pads[sig] = num
            else:
                net = sig.upper()
                to_mcu.append((net, "–"))
                issues.append(("warning", f"the chip's {sig} goes to J1 only (nothing on the module drives or reads it)"))
            nets[num] = net
            pins.append(Pin(num, e.pad or sig.upper(), ptype, side))
    if fp.pads[-1].number == str(len(c.package.pinmap) + 1):
        ep_num = fp.pads[-1].number
        nets[ep_num] = GND if c.package.exposed_pad.upper() in ("VSS", "GND") else ""
        pins.append(Pin(ep_num, "EP", "passive", "bottom"))
    chip = Part("U1", c.chip.name, fp, pins, nets, symbol=c.chip.name, description=f"The chip ({c.chip.top_module}, {c.chip.snapshot[:19]}…)",
                group="chip")
    parts.append(chip)

    nc = {"C": 0, "R": 0, "U": 1, "D": 0, "X": 0}

    def ref(kind: str) -> str:
        nc[kind] += 1
        return f"{kind}{nc[kind]}"

    for pad in vdd_pads:
        parts.append(_c(ref("C"), "100n", VCC, GND, f"U1:{pad}"))
    if vdd_pads:
        parts.append(_c(ref("C"), "1u", VCC, GND, f"U1:{vdd_pads[0]}"))
    if clk_pad:
        hz = c.electrical.max_clock_hz or c.clock.frequency_hz
        maker, mpn = OSC_MPN.get(hz, ("", ""))
        x = ref("X")
        parts.append(Part(x, f"{hz / 1e6:g} MHz", fps.oscillator3225(),
                          [Pin("1", "EN", "input", "left"), Pin("2", "GND", "power_in", "bottom"), Pin("3", "OUT", "output", "right"),
                           Pin("4", "VDD", "power_in", "top")],
                          {"1": VCC, "2": GND, "3": "CLK", "4": VCC}, symbol="Oscillator", mpn=mpn, manufacturer=maker,
                          description=f"Clock oscillator, {hz / 1e6:g} MHz, 3.3 V CMOS, 3.2x2.5 mm", near=f"U1:{clk_pad}"))
        parts.append(_c(ref("C"), "100n", VCC, GND, f"{x}:4"))
        if not mpn:
            issues.append(("warning", f"pick a 3.3 V CMOS oscillator at {hz / 1e6:g} MHz in a 3.2x2.5 mm package for {x}"))
    if rst_pad:
        net = nets[rst_pad]
        if c.reset.active_low:
            parts.append(_r(ref("R"), "10k", net, VCC, f"U1:{rst_pad}"))
            parts.append(_c(ref("C"), "100n", net, GND, f"U1:{rst_pad}"))
        else:
            parts.append(_r(ref("R"), "10k", net, GND, f"U1:{rst_pad}"))
            parts.append(_c(ref("C"), "100n", net, VCC, f"U1:{rst_pad}"))
    led = catalog.component("led")["part"]
    for sig, pad in led_pads.items():
        net = nets[pad]
        r = ref("R")
        parts.append(_r(r, "330", net, f"{net}_A", f"U1:{pad}"))
        parts.append(Part(ref("D"), "LED", fps.led0603(), [Pin("1", "K", "passive", "right"), Pin("2", "A", "passive", "left")],
                          {"1": GND, "2": f"{net}_A"}, symbol="LED", mpn=led["mpn"], manufacturer=led["manufacturer"],
                          description=led["description"], near=f"{r}:2"))

    # What the analog side's drives switch on the module (the fan's MOSFET, its diode, its connector).
    supplies: list[tuple[str, str]] = []
    if b.analog is not None:
        try:
            model = catalog.analog_model(b.analog.model)
        except catalog.CatalogError as e:
            return None, [("error", str(e))]
        for drive, pin in b.analog.drives.items():
            spec = ((model.get("drives") or {}).get(drive) or {}).get("pcb") or {}
            if spec.get("circuit") != "low_side_switch":
                issues.append(("warning", f"the analog side's {drive} has no circuit on the module"))
                continue
            gate_net = nets.get(pin_of(c, pin), "")
            if not gate_net:
                issues.append(("error", f"the chip pin {pin} that switches the {drive} isn't on the package"))
                continue
            supply = "+5V" if str(spec.get("supply", "5V")).upper() == "5V" else VCC
            if supply == "+5V" and ("+5V", "5V") not in supplies:
                supplies.append(("+5V", "5V"))
            load = str(spec.get("load", drive))
            g, sw = f"{load.upper()}_G", f"{load.upper()}_SW"
            rg = ref("R")
            parts.append(_r(rg, "100", gate_net, g, f"U1:{pin_of(c, pin)}"))
            q = f"Q{len([p for p in parts if p.ref.startswith('Q')]) + 1}"
            parts.append(Part(q, "AO3400A", fps.sot23(), [Pin("1", "G", "input", "left"), Pin("2", "S", "passive", "bottom"), Pin("3", "D", "passive", "top")],
                              {"1": g, "2": GND, "3": sw}, symbol="NMOS", mpn="AO3400A", manufacturer="Alpha & Omega",
                              description=f"N-MOSFET, 30 V, 5.7 A, logic level: switches the {load}", near=f"{rg}:2"))
            parts.append(_r(ref("R"), "100k", g, GND, f"{q}:1"))
            dref = ref("D")
            parts.append(Part(dref, "B5819W", fps.sod123(), [Pin("1", "K", "passive", "top"), Pin("2", "A", "passive", "bottom")],
                              {"1": supply, "2": sw}, symbol="D", mpn="B5819W", manufacturer="Diodes Inc.",
                              description=f"Schottky diode, 40 V, 1 A: the {load}'s flyback", near=f"{q}:3"))
            jn = f"J{2 + sum(1 for p in parts if p.ref.startswith('J') and p.ref != 'J1')}"
            parts.append(Part(jn, load.capitalize(), fps.header1x(2), [Pin("1", f"{supply}", "passive", "left"), Pin("2", sw, "passive", "left")],
                              {"1": supply, "2": sw}, symbol="Conn_01x02", description=f"The {load}: connector, 2 pins, 2.54 mm", near=f"{dref}:1"))
            parts.append(_c(ref("C"), "10u", supply, GND, f"{dref}:1"))
            issues.append(("info", f"the {load} runs from {supply} (J1) through {q}, switched by the chip's {pin}"))

    buses: dict[str, str] = {}  # bus -> the sensor ref whose pins get the pull-ups
    for s in b.sensors:
        comp = catalog.component(s.part)
        pcb = comp.get("pcb")
        if not pcb:
            issues.append(("error", f"{s.part} ({s.name}) has no land pattern in Forge yet: use a part that has one (tmp1075)"))
            continue
        if pcb["package"] != "soic8":
            issues.append(("error", f"{s.part}: Forge has no {pcb['package']} land pattern"))
            continue
        u = ref("U")
        bus = plat["i2c"][s.bus]
        sda, scl = f"{s.bus.upper()}_SDA", f"{s.bus.upper()}_SCL"
        index = s.address - 0x48
        pnets: dict[str, str] = {}
        ppins: list[Pin] = []
        vdd_pad = sda_pad = scl_pad = ""
        for num, spec in pcb["pins"].items():
            role = spec["role"]
            net = {"sda": sda, "scl": scl, "gnd": GND, "vdd": VCC, "nc": ""}.get(role)
            if net is None and role.startswith("addr"):
                net = VCC if (index >> int(role[4:])) & 1 else GND
            pnets[str(num)] = net or ""
            ppins.append(Pin(str(num), spec["name"], spec["type"], spec.get("side", "left")))
            vdd_pad = str(num) if role == "vdd" else vdd_pad
            sda_pad = str(num) if role == "sda" else sda_pad
            scl_pad = str(num) if role == "scl" else scl_pad
        part = comp["part"]
        parts.append(Part(u, part["mpn"].removesuffix("DR").removesuffix("R") or s.part, fps.soic(), ppins, pnets, symbol=s.part.upper(),
                          mpn=part["mpn"], manufacturer=part["manufacturer"], description=f"{part['description']} ({s.name}, address {s.address:#04x})",
                          group="right"))
        parts.append(_c(ref("C"), "100n", VCC, GND, f"{u}:{vdd_pad}"))
        if s.bus not in buses:
            buses[s.bus] = u
            parts.append(_r(ref("R"), "4.7k", scl, VCC, f"{u}:{scl_pad}"))
            parts.append(_r(ref("R"), "4.7k", sda, VCC, f"{u}:{sda_pad}"))
            to_mcu += [(scl, bus["scl"]), (sda, bus["sda"])]

    # J1: the supply, then each signal to the MCU board.
    order = [(VCC, "3V"), (GND, "GND")] + supplies + to_mcu
    header = fps.header1x(len(order))
    jn = {str(i + 1): net for i, (net, _) in enumerate(order)}
    # Each pin's name says where it goes on the MCU board ("SPI_SCK/PA5"); the silkscreen too.
    jp = [Pin(str(i + 1), f"{net}/{mcu}" if mcu not in ("–", "") else net, "passive", "left") for i, (net, mcu) in enumerate(order)]
    parts.insert(1, Part("J1", "To MCU board", header, jp, jn, symbol=f"Conn_01x{len(order):02d}",
                         mpn="", description=f"Pin header 1x{len(order)}, 2.54 mm, to the {plat['title']}", group="connector"))
    d = Design(title=title, parts=parts, power_nets=[GND, VCC] + [s for s, _ in supplies])
    d.notes = [f"J1 pin {i + 1}: {net} -> {mcu}" for i, (net, mcu) in enumerate(order)]
    d.notes.append(f"made from hardware.yaml (chip {c.chip.name}, {c.chip.snapshot[:19]}…) and system.yaml ({b.platform})")
    return d, issues
