"""The board as a picture (SVG): the MCU, the chip, the sensors, the LEDs and the console, with
the pins that connect them, drawn from system.yaml and hardware.yaml. What the System page
shows; the schematic proper comes in a later version."""

from __future__ import annotations

from html import escape

from chipova_forge.contract.contract import Contract
from chipova_forge.system import board as board_mod
from chipova_forge.system import catalog

W, H = 860, 430


def _box(x: int, y: int, w: int, h: int, title: str, sub: str, color: str) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#0f172a" stroke="{color}" stroke-width="1.5"/>'
            f'<text x="{x + w / 2}" y="{y + 22}" text-anchor="middle" fill="#e2e8f0" font-size="13" font-weight="700">{escape(title)}</text>'
            f'<text x="{x + w / 2}" y="{y + 39}" text-anchor="middle" fill="#94a3b8" font-size="10">{escape(sub)}</text>')


def _wire(x1: float, y1: float, x2: float, y2: float, label_a: str, label_b: str, color: str = "#64748b") -> str:
    mx = (x1 + x2) / 2
    return (f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" fill="none" stroke="{color}" stroke-width="1.4"/>'
            f'<text x="{x1 + (6 if x2 > x1 else -6)}" y="{y1 - 4}" text-anchor="{"start" if x2 > x1 else "end"}" fill="#cbd5e1" font-size="9.5" font-family="ui-monospace,monospace">{escape(label_a)}</text>'
            f'<text x="{x2 + (-6 if x2 > x1 else 6)}" y="{y2 - 4}" text-anchor="{"end" if x2 > x1 else "start"}" fill="#cbd5e1" font-size="9.5" font-family="ui-monospace,monospace">{escape(label_b)}</text>')


def render(b: board_mod.Board, c: Contract) -> str:
    p = catalog.platform(b.platform)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" font-family="ui-sans-serif,system-ui,sans-serif">']
    mcu_x, mcu_y, mcu_w, mcu_h = 300, 70, 220, 290
    parts.append(_box(mcu_x, mcu_y, mcu_w, mcu_h, p["mcu"]["part"], f"{p['mcu']['core']} · {p['mcu']['clock_hz'] // 1_000_000} MHz", "#f59e0b"))
    chip_x, chip_y, chip_w, chip_h = 610, 70, 220, 200
    parts.append(_box(chip_x, chip_y, chip_w, chip_h, c.chip.name, f"{c.chip.top_module} · {c.interface.protocol.upper()}", "#2dd4bf"))
    # MCU <-> chip: the interface, then interrupt pins
    y = chip_y + 70
    if c.interface.protocol == "spi":
        spi = p["spi"][b.chip.interface]
        for role, mcu_pin in (("sclk", spi["sck"]), ("mosi", spi["mosi"]), ("miso", spi["miso"]), ("cs_n", b.chip.chip_select)):
            parts.append(_wire(mcu_x + mcu_w, y, chip_x, y, f"{mcu_pin}", c.interface.ports.get(role, role), "#2dd4bf"))
            y += 22
    else:
        win = p["bus_windows"].get(b.chip.interface, {})
        parts.append(_wire(mcu_x + mcu_w, y, chip_x, y, f"FSMC {b.chip.interface.split('_')[-1].upper()}", f"bus @ {win.get('base', 0):#x}", "#2dd4bf"))
        y += 22
        if c.interface.protocol == "none":
            parts.append(f'<text x="{chip_x + chip_w / 2}" y="{chip_y + 55}" text-anchor="middle" fill="#fbbf24" font-size="9.5">in Forge\'s register harness (simulation)</text>')
    for chip_pin, mcu_pin in b.chip.irq.items():
        parts.append(_wire(mcu_x + mcu_w, y, chip_x, y, mcu_pin + " (EXTI)", chip_pin, "#f472b6"))
        y += 22
    # Chip LEDs
    ly = chip_y + chip_h + 30
    for i, pin in enumerate(b.chip.leds):
        cx = chip_x + 40 + i * 70
        parts.append(f'<line x1="{cx}" y1="{chip_y + chip_h}" x2="{cx}" y2="{ly}" stroke="#64748b" stroke-width="1.2"/>'
                     f'<circle cx="{cx}" cy="{ly + 10}" r="9" fill="#78350f" stroke="#fbbf24"/>'
                     f'<text x="{cx}" y="{ly + 36}" text-anchor="middle" fill="#cbd5e1" font-size="9.5" font-family="ui-monospace,monospace">{escape(pin)}</text>')
    # Sensors on the left
    sy = 70
    for s in b.sensors:
        comp = catalog.component(s.part)
        parts.append(_box(30, sy, 190, 70, s.name, f"{comp['part']['mpn']} @ {s.address:#04x}", "#a78bfa"))
        bus = p["i2c"].get(s.bus, {})
        parts.append(_wire(220, sy + 48, mcu_x, sy + 48, f"{s.bus.upper()}", f"{bus.get('scl', '')}/{bus.get('sda', '')}", "#a78bfa"))
        sy += 100
    # Console
    uart = p["uarts"][b.console]
    parts.append(_box(30, 300, 190, 60, "Console (device API)", f"{b.console.upper()} · 115200 8N1", "#38bdf8"))
    parts.append(_wire(220, 330, mcu_x, 330, "", f"{uart['tx']}/{uart['rx']}", "#38bdf8"))
    # Board LEDs under the MCU
    for i, (name, led) in enumerate(b.leds.items()):
        cx = mcu_x + 50 + i * 110
        col = {"green": "#10b981", "orange": "#f59e0b", "red": "#f43f5e", "blue": "#38bdf8"}.get(led, "#10b981")
        parts.append(f'<circle cx="{cx}" cy="{mcu_y + mcu_h - 28}" r="8" fill="{col}" opacity="0.35" stroke="{col}"/>'
                     f'<text x="{cx}" y="{mcu_y + mcu_h - 8}" text-anchor="middle" fill="#cbd5e1" font-size="9" font-family="ui-monospace,monospace">{escape(name)} {escape(p["leds"][led]["pin"])}</text>')
    parts.append("</svg>")
    return "".join(parts)
