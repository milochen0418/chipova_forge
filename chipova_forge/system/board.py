"""The board (``system.yaml``): the MCU platform, how the chip is wired to it, the sensors, the
UART the device API talks to, the LEDs to show. Checked against the platform and the chip's
contract; everything that runs is made from it:

- ``board.h`` for the firmware (where every pin and peripheral is),
- the chip's co-simulation spec (which of its pins go to the MCU, which light LEDs),
- the simulation: Renode's board description (``board.repl``), the session script
  (``session.resc``) and the session agent's map (``session.json``).

    platform: stm32f4_discovery
    chip:
      interface: spi1          # an SPI controller (an SPI chip) or a bus window (fsmc_ne2 ...)
      chip_select: PA4         # SPI: the MCU pin wired to the chip's select
      irq: {irq: PB1}          # the chip's interrupt pins -> MCU pins (external interrupts)
      leds: [alarm_led]        # the chip's pins that light an LED on the board
    sensors:
      - {name: temperature, part: tmp1075, bus: i2c1, address: 0x48, initial: {celsius: 25}}
    console: usart2
    leds: {heartbeat: green, alarm: red}
    firmware: {sample_ms: 100}  # becomes BOARD_SAMPLE_MS in board.h
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml
from pydantic import BaseModel, Field

from chipova_forge.contract.contract import Contract, Issue
from chipova_forge.contract.registers import RegisterMap
from chipova_forge.cosim.wrapper import Apb3, CosimSpec, Pin, Spi
from chipova_forge.system import catalog

CHIP_SIZE_DEFAULT = 0x100
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ChipWiring(BaseModel):
    interface: str
    chip_select: str = ""
    irq: dict[str, str] = Field(default_factory=dict)
    leds: list[str] = Field(default_factory=list)


class SensorPart(BaseModel):
    name: str
    part: str
    bus: str
    address: int = 0x48
    initial: dict[str, float] = Field(default_factory=dict)


class AnalogPart(BaseModel):
    """The board's analog side: one of Forge's analog models (templates/analog), run by ngspice
    in step with the board (every ``step_ms`` of virtual time)."""

    model: str
    step_ms: int = 50
    drives: dict[str, str] = Field(default_factory=dict)  # the model's drive -> the chip pin (one of chip.leds)
    sensors: dict[str, str] = Field(default_factory=dict)  # the board's sensor -> the model's output
    inputs: dict[str, float] = Field(default_factory=dict)  # the model's inputs at the start
    parameters: dict[str, float] = Field(default_factory=dict)  # the model's parameters, if not its own


class Board(BaseModel):
    platform: str = "stm32f4_discovery"
    chip: ChipWiring
    sensors: list[SensorPart] = Field(default_factory=list)
    console: str = "usart2"
    leds: dict[str, str] = Field(default_factory=dict)
    firmware: dict[str, int] = Field(default_factory=dict)
    analog: AnalogPart | None = None


def from_yaml(text: str) -> Board:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("system.yaml isn't a mapping")
    return Board.model_validate(data)


def to_yaml(b: Board) -> str:
    head = (
        "# Chipova Forge: the board. The MCU platform, how the chip is wired to it, the sensors, the\n"
        "# UART the device API talks to, the LEDs to show. Forge makes the firmware's board.h and the\n"
        "# simulation's board from this file, and checks it against the platform and hardware.yaml.\n"
    )
    return head + yaml.safe_dump(b.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100)


# ------------------------------------------------------------------ checks


def check(b: Board, c: Contract | None) -> list[Issue]:
    issues: list[Issue] = []

    def err(where: str, msg: str, fix: str = "") -> None:
        issues.append(Issue(level="error", where=where, message=msg, fix=fix))

    try:
        p = catalog.platform(b.platform)
    except catalog.CatalogError as e:
        err("platform", str(e))
        return issues
    used: dict[str, str] = {pin: why for pin, why in (p.get("gpio") or {}).get("reserved", {}).items()}

    def take(pin: str, why: str, where: str) -> None:
        try:
            catalog.parse_pin(pin)
        except catalog.CatalogError as e:
            err(where, str(e))
            return
        pin = pin.upper()
        if pin in used and used[pin] != why:
            err(where, f"{pin} is already {used[pin]}", "choose another pin")
        used[pin] = why

    uart = (p.get("uarts") or {}).get(b.console)
    if uart is None:
        err("console", f"{b.platform} has no UART {b.console!r} (it has {', '.join(p.get('uarts') or {})})")
    else:
        take(uart["tx"], f"{b.console} TX", "console")
        take(uart["rx"], f"{b.console} RX", "console")

    proto = c.interface.protocol if c else ""
    w = b.chip
    if proto == "spi":
        spi = (p.get("spi") or {}).get(w.interface)
        if spi is None:
            err("chip.interface", f"the chip talks SPI: interface must be one of {', '.join(p.get('spi') or {})}")
        else:
            for role in ("sck", "miso", "mosi"):
                take(spi[role], f"{w.interface} {role.upper()}", "chip.interface")
        if not w.chip_select:
            err("chip.chip_select", "an SPI chip needs a chip-select pin (e.g. PA4)")
        else:
            take(w.chip_select, "the chip's select", "chip.chip_select")
    elif proto in ("apb3", "none"):
        if w.interface not in (p.get("bus_windows") or {}):
            err("chip.interface", f"the chip sits on the memory bus: interface must be one of {', '.join(p.get('bus_windows') or {})}")
        if w.chip_select:
            err("chip.chip_select", "only SPI chips have a chip-select pin here")

    exti = (p.get("gpio") or {}).get("exti") or {}
    for chip_pin, mcu_pin in w.irq.items():
        if c is not None:
            pu = c.port(chip_pin)
            if pu is None or pu.direction != "out" or pu.width != 1:
                err("chip.irq", f"{chip_pin!r} isn't a 1-bit output of the chip")
        try:
            _, n = catalog.parse_pin(mcu_pin)
        except catalog.CatalogError as e:
            err("chip.irq", str(e))
            continue
        if n not in exti and str(n) not in exti:
            err("chip.irq", f"{mcu_pin}: the firmware templates take interrupts on pins 0-4 of a port (EXTI0-4)")
        take(mcu_pin, f"the chip's {chip_pin}", "chip.irq")
    for chip_pin in w.leds:
        if c is not None:
            pu = c.port(chip_pin)
            if pu is None or pu.direction != "out":
                err("chip.leds", f"{chip_pin!r} isn't an output of the chip")
    if c is not None:
        for pu in c.irqs:
            if pu.port not in w.irq:
                issues.append(Issue(level="warning", where="chip.irq", message=f"the chip's interrupt pin {pu.port} isn't wired to the MCU"))

    names: set[str] = set()
    for s in b.sensors:
        where = f"sensors.{s.name}"
        if not _NAME.match(s.name) or s.name in names:
            err(where, "a sensor's name: lowercase letters, digits and _, unique")
        names.add(s.name)
        try:
            comp = catalog.component(s.part)
        except catalog.CatalogError as e:
            err(where, str(e))
            continue
        if comp.get("bus") == "i2c":
            bus = (p.get("i2c") or {}).get(s.bus)
            if bus is None:
                err(where, f"{b.platform} has no I2C bus {s.bus!r}")
            else:
                take(bus["scl"], f"{s.bus} SCL", where)
                take(bus["sda"], f"{s.bus} SDA", where)
            if s.address not in comp.get("addresses", [s.address]):
                err(where, f"a {s.part} answers at {', '.join(hex(a) for a in comp['addresses'])}")
        for k, v in s.initial.items():
            st = (comp.get("settings") or {}).get(k)
            if st is None:
                err(where, f"a {s.part} has no setting {k!r}")
            elif not st["min"] <= v <= st["max"]:
                err(where, f"{k} is {st['min']}..{st['max']} {st.get('unit', '')}")
    for name, led in b.leds.items():
        if led not in (p.get("leds") or {}):
            err(f"leds.{name}", f"{b.platform} has no LED {led!r} (it has {', '.join(p.get('leds') or {})})")
    for k, v in b.firmware.items():
        if not re.match(r"^[a-z][a-z0-9_]{0,31}$", k) or not isinstance(v, int):
            err("firmware", f"firmware settings are whole numbers with lowercase names ({k!r})")
    if b.analog is not None:
        issues += _check_analog(b, c)
    return issues


# ------------------------------------------------------------------ the chip's simulation


def chip_outputs(b: Board, c: Contract) -> list[str]:
    """The chip's output pins Renode sees, in order (their GPIO numbers): interrupts, then the
    rest (an LED on some of them)."""
    outs = [p.port for p in c.irqs] + [p.port for p in c.outputs if p.width == 1]
    return outs


def chip_inputs(c: Contract) -> list[str]:
    return [p.port for p in c.inputs if p.width == 1]


def cosim_spec(c: Contract, sources: tuple[str, ...], top: str | None = None, spec_ports: dict[str, str] | None = None) -> CosimSpec:
    """How the chip library is built (sim_main.cpp): its bus or SPI pins, its pins to Renode."""
    outs = tuple(Pin(name, name, 0, i) for i, name in enumerate([p.port for p in c.irqs] + [p.port for p in c.outputs if p.width == 1]))
    ins = tuple(Pin(name, name, 0, i) for i, name in enumerate(chip_inputs(c)))
    iface = c.interface
    apb = spi = None
    if iface.protocol == "spi":
        spi = Spi(sclk=iface.ports["sclk"], mosi=iface.ports["mosi"], miso=iface.ports["miso"], cs_n=iface.ports["cs_n"],
                  half_period=c.simulation.spi_half_period_cycles)
    else:
        ports = spec_ports or iface.ports
        apb = Apb3(psel=ports["psel"], penable=ports["penable"], pwrite=ports["pwrite"], paddr=ports["paddr"],
                   pwdata=ports["pwdata"], prdata=ports["prdata"], pready=ports["pready"], pslverr=ports.get("pslverr", ""))
    if spec_ports is not None:
        # The register harness around a chip with no bus: its own clock and reset (active low).
        clock, reset, active_low = "clk", "rst_n", True
    else:
        clock, reset, active_low = c.clock.port, c.reset.port, c.reset.active_low
    return CosimSpec(top=top or c.chip.top_module, sources=sources, clock=clock, reset=reset,
                     reset_active_low=active_low, apb3=apb, spi=spi, inputs=ins, outputs=outs,
                     frequency_hz=c.simulation.frequency_hz)


# ------------------------------------------------------------------ firmware: board.h


def _port_base(port: str) -> str:
    return f"GPIO{port}_BASE"


def board_h(b: Board, c: Contract, regs: RegisterMap | None) -> str:
    p = catalog.platform(b.platform)
    lines = [
        "/* Generated by Chipova Forge from system.yaml and hardware.yaml: where things are on this board.",
        " * Don't edit: change system.yaml and build again. */",
        "#ifndef BOARD_H",
        "#define BOARD_H",
        "",
        f"/* {p['title']}; the chip: {c.chip.name} ({c.chip.top_module}), {c.interface.protocol} */",
    ]

    def d(name: str, value: Any, comment: str = "") -> None:
        lines.append(f"#define {name} {value}" + (f"  /* {comment} */" if comment else ""))

    w = b.chip
    if c.interface.protocol == "spi":
        spi = p["spi"][w.interface]
        d("BOARD_CHIP_INTERFACE_SPI", 1)
        d("BOARD_CHIP_SPI", spi["base"])
        d("BOARD_CHIP_SPI_AF", f"{spi['af']}u")
        d("BOARD_CHIP_SPI_CLOCK_ON()", f"({spi['clock']})")
        for role, key in (("SCK", "sck"), ("MISO", "miso"), ("MOSI", "mosi")):
            port, n = catalog.parse_pin(spi[key])
            d(f"BOARD_CHIP_{role}_PORT", _port_base(port))
            d(f"BOARD_CHIP_{role}_PIN", f"{n}u")
        port, n = catalog.parse_pin(w.chip_select)
        d("BOARD_CHIP_CS_PORT", _port_base(port))
        d("BOARD_CHIP_CS_PIN", f"{n}u")
    else:
        win = p["bus_windows"][w.interface]
        d("BOARD_CHIP_INTERFACE_BUS", 1)
        d("BOARD_CHIP_BASE", f"{win['base']:#010x}u", w.interface)
        d("BOARD_CHIP_FSMC_BANK", f"{win['bank']}u")
    exti = (p.get("gpio") or {}).get("exti") or {}
    for i, (chip_pin, mcu_pin) in enumerate(sorted(w.irq.items())):
        port, n = catalog.parse_pin(mcu_pin)
        info = exti.get(n) or exti.get(str(n)) or {}
        pre = "BOARD_CHIP_IRQ" if i == 0 else f"BOARD_CHIP_{chip_pin.upper()}"
        d(f"{pre}_PORT", _port_base(port), f"the chip's {chip_pin} -> {mcu_pin}")
        d(f"{pre}_PORT_INDEX", f"{ord(port) - ord('A')}u")
        d(f"{pre}_PIN", f"{n}u")
        d(f"{pre}n", f"{info.get('irq', 0)}u")
        d(f"{pre}Handler", info.get("handler", "Default_Handler"))
    uart = p["uarts"][b.console]
    d("BOARD_UART", uart["base"], f"{b.console}: the console the device API talks to")
    d("BOARD_UART_IRQn", f"{uart['irq']}u")
    d("BOARD_UART_IRQHandler", uart["handler"])
    d("BOARD_UART_CLOCK_ON()", f"({uart['clock']})")
    d("BOARD_UART_AF", f"{uart['af']}u")
    for role, key in (("TX", "tx"), ("RX", "rx")):
        port, n = catalog.parse_pin(uart[key])
        d(f"BOARD_UART_{role}_PORT", _port_base(port))
        d(f"BOARD_UART_{role}_PIN", f"{n}u")
    for i, s in enumerate(b.sensors):
        comp = catalog.component(s.part)
        pre = "BOARD_SENSOR" if i == 0 else f"BOARD_SENSOR_{s.name.upper()}"
        if comp.get("bus") == "i2c":
            bus = p["i2c"][s.bus]
            d(f"{pre}_I2C", bus["base"], f"{s.name}: {comp['title']}")
            d(f"{pre}_I2C_CLOCK_ON()", f"({bus['clock']})")
            d(f"{pre}_I2C_AF", f"{bus['af']}u")
            for role, key in (("SCL", "scl"), ("SDA", "sda")):
                port, n = catalog.parse_pin(bus[key])
                d(f"{pre}_{role}_PORT", _port_base(port))
                d(f"{pre}_{role}_PIN", f"{n}u")
            d(f"{pre}_ADDR", f"{s.address:#04x}u")
    for name, led in b.leds.items():
        port, n = catalog.parse_pin(p["leds"][led]["pin"])
        d(f"BOARD_LED_{name.upper()}_PORT", _port_base(port), f"{led} LED")
        d(f"BOARD_LED_{name.upper()}_PIN", f"{n}u")
    for k, v in b.firmware.items():
        d(f"BOARD_{k.upper()}", f"{int(v)}u")
    if regs is not None:
        d("BOARD_CHIP_REGS_SIZE", f"{regs.size:#x}u", f"{regs.name}")
    lines += ["", "#endif", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ the simulation


def _led_name(chip_pin: str) -> str:
    return f"chip_{chip_pin}"


def _check_analog(b: Board, c: Contract | None) -> list[Issue]:
    a = b.analog
    assert a is not None
    out: list[Issue] = []

    def err(where: str, msg: str) -> None:
        out.append(Issue(level="error", where=f"analog.{where}", message=msg))

    try:
        m = catalog.analog_model(a.model)
    except catalog.CatalogError as e:
        err("model", str(e))
        return out
    if not 10 <= a.step_ms <= 1000:
        err("step_ms", "a step is 10 to 1000 ms of virtual time")
    for drive, pin in a.drives.items():
        if drive not in (m.get("drives") or {}):
            err("drives", f"{a.model} has no drive {drive!r} (it has {', '.join(m.get('drives') or {})})")
        if pin not in b.chip.leds:
            err("drives", f"{pin}: a pin that drives the analog side is one of chip.leds (the board shows it, the simulation reads it there)")
    for sensor, output in a.sensors.items():
        s = next((x for x in b.sensors if x.name == sensor), None)
        o = (m.get("outputs") or {}).get(output)
        if s is None:
            err("sensors", f"the board has no sensor {sensor!r}")
        elif o is None:
            err("sensors", f"{a.model} has no output {output!r}")
        else:
            units = {st.get("unit") for st in (catalog.component(s.part).get("settings") or {}).values()}
            if o.get("unit") not in units:
                err("sensors", f"{sensor} reads {', '.join(str(u) for u in units)}, {output} is in {o.get('unit')}")
    for name, value in a.inputs.items():
        spec = (m.get("inputs") or {}).get(name)
        if spec is None:
            err("inputs", f"{a.model} has no input {name!r}")
        elif not spec["min"] <= value <= spec["max"]:
            err("inputs", f"{name} is {spec['min']}..{spec['max']} {spec.get('unit', '')}")
    for name in a.parameters:
        if name not in (m.get("parameters") or {}):
            err("parameters", f"{a.model} has no parameter {name!r}")
    return out


def analog_netlist(b: Board) -> list[str]:
    """The analog model's SPICE circuit with this board's parameters."""
    a = b.analog
    assert a is not None
    m = catalog.analog_model(a.model)
    values = {k: v["value"] for k, v in (m.get("parameters") or {}).items()}
    values.update(a.parameters)
    values["title"] = m.get("title", a.model)
    values["tstep"] = f"{max(1, a.step_ms // 10)}m"
    return m["netlist"].format(**values).strip().splitlines()


def analog_session(b: Board, c: Contract) -> dict[str, Any] | None:
    """What the session agent needs to run the analog side in step with the board."""
    a = b.analog
    if a is None:
        return None
    m = catalog.analog_model(a.model)
    leds = leds_to_watch(b, c)
    sensors = []
    for sensor, output in a.sensors.items():
        s = next(x for x in b.sensors if x.name == sensor)
        o = m["outputs"][output]
        setting = next(st for st in (catalog.component(s.part).get("settings") or {}).values() if st.get("unit") == o.get("unit"))
        sensors.append({"sensor": sensor, "output": output, "property": setting["property"], "scale": setting.get("scale", 1)})
    return {
        "model": a.model,
        "title": m.get("title", a.model),
        "netlist": analog_netlist(b),
        "step": a.step_ms / 1000.0,
        "inputs": {k: {"source": v["source"], "title": v.get("title", k), "unit": v.get("unit", ""), "min": v["min"], "max": v["max"],
                       "value": float(a.inputs.get(k, v.get("default", 0)))} for k, v in (m.get("inputs") or {}).items()},
        "drives": {k: {"source": m["drives"][k]["source"], "title": m["drives"][k].get("title", k), "pin": pin,
                       "path": leds[_led_name(pin)], "on": m["drives"][k].get("on", 1.0), "off": m["drives"][k].get("off", 0.0)}
                   for k, pin in a.drives.items()},
        "outputs": {k: {"node": v["node"], "title": v.get("title", k), "unit": v.get("unit", "")} for k, v in (m.get("outputs") or {}).items()},
        "sensors": sensors,
    }


def board_repl(b: Board, c: Contract) -> tuple[str, list[str]]:
    """Renode's description of the board (loaded after the platform's) and the component source
    files it needs compiled first."""
    p = catalog.platform(b.platform)
    outs = chip_outputs(b, c)
    lines = [
        "// Generated by Chipova Forge from system.yaml and hardware.yaml.",
        'using "/work/session/platform/platform.repl"',
        "",
    ]
    sources: list[str] = []
    sim = c.simulation
    limit = max(100, sim.frequency_hz // 1000)  # sync with Renode every 1 ms of virtual time
    gpio_out = []
    for i, port_name in enumerate(outs):
        target = ""
        if port_name in b.chip.irq:
            port, n = catalog.parse_pin(b.chip.irq[port_name])
            target = f"gpioPort{port}@{n}"
        elif port_name in b.chip.leds:
            target = f"{_led_name(port_name)}@0"
        if target:
            gpio_out.append(f"    {i} -> {target}")
    signals = f"    cosimToRenodeSignalRange: <0, +{len(outs)}>" if outs else ""
    if c.interface.protocol == "spi":
        sources.append("ForgeSpiChip.cs")
        lines += [
            f"chip: CoSimulated.ForgeSpiChip @ {b.chip.interface}",
            f"    frequency: {sim.frequency_hz}",
            f"    limitBuffer: {limit}",
            "    timeout: 10000",
            "    chipSelectInput: 0",
        ]
    else:
        win = p["bus_windows"][b.chip.interface]
        lines += [
            f"chip: CoSimulated.CoSimulatedPeripheral @ sysbus <{win['base']:#x}, +{CHIP_SIZE_DEFAULT:#x}>",
            f"    frequency: {sim.frequency_hz}",
            f"    limitBuffer: {limit}",
            "    timeout: 10000",
        ]
    if signals:
        lines.append(signals)
    lines += gpio_out
    lines.append("")
    for port_name in b.chip.leds:
        lines.append(f"{_led_name(port_name)}: Miscellaneous.LED @ gpioPortD")
    if b.chip.leds:
        lines.append("")
    for s in b.sensors:
        comp = catalog.component(s.part)
        ren = comp.get("renode") or {}
        if ren.get("source") and ren["source"] not in sources:
            sources.insert(0, ren["source"])
        lines.append(f"{s.name}: {ren['type']} @ {s.bus} {s.address:#04x}")
    if b.sensors:
        lines.append("")
    # GPIO pins that drive the chip's inputs: the chip select (SPI). A pin's list here replaces
    # the platform's, so it is written whole (the platform's connections + the chip).
    if c.interface.protocol == "spi":
        port, n = catalog.parse_pin(b.chip.chip_select)
        lists = catalog.renode_pin_lists(b.platform)
        pins = {k: list(v) for k, v in (lists.get(f"gpioPort{port}") or {}).items()}
        pins.setdefault(n, [f"exti@{n}"])
        pins[n] = pins[n] + ["chip@0"]
        lines.append(f"gpioPort{port}:")
        for k in sorted(pins):
            lines.append(f"    {k} -> " + " | ".join(pins[k]))
        lines.append("")
    return "\n".join(lines), sources


def chip_path(b: Board, c: Contract) -> str:
    return f"sysbus.{b.chip.interface}.chip" if c.interface.protocol == "spi" else "sysbus.chip"


def leds_to_watch(b: Board, c: Contract) -> dict[str, str]:
    """name -> Renode path of every LED the session shows."""
    p = catalog.platform(b.platform)
    out = {name: p["leds"][led]["renode"] for name, led in b.leds.items()}
    for port_name in b.chip.leds:
        out[_led_name(port_name)] = f"sysbus.gpioPortD.{_led_name(port_name)}"
    return out


def session_resc(b: Board, c: Contract, component_sources: list[str]) -> str:
    p = catalog.platform(b.platform)
    lines = [
        "# Generated by Chipova Forge: builds the board in Renode (run by the session agent).",
        "include @/work/agent/hooks.py",
    ]
    for src in component_sources:
        lines.append(f"include @/work/session/components/{src}")
    lines += [
        'mach create "board"',
        "machine LoadPlatformDescription @/work/session/board.repl",
        f"{chip_path(b, c)} SimulationFilePathLinux @/work/session/libVchip.so",
        "sysbus LoadELF @/work/session/firmware.elf",
        f'forge_watch_uart "{p["uarts"][b.console]["renode"]}" "console"',
    ]
    for name, path in leds_to_watch(b, c).items():
        lines.append(f'forge_watch_led "{path}" "{name}"')
    return "\n".join(lines) + "\n"


def session_json(b: Board, c: Contract, regs: RegisterMap | None, autostart: bool = True) -> dict[str, Any]:
    p = catalog.platform(b.platform)
    sensors = {}
    initial: dict[str, dict[str, int]] = {}
    for s in b.sensors:
        comp = catalog.component(s.part)
        props = {}
        for key, st in (comp.get("settings") or {}).items():
            scale = st.get("scale", 1)
            props[st["property"]] = {"min": int(st["min"] * scale), "max": int(st["max"] * scale), "setting": key, "scale": scale}
            value = s.initial.get(key, st.get("default"))
            if value is not None:
                initial.setdefault(s.name, {})[st["property"]] = int(round(float(value) * scale))
        sensors[s.name] = {"path": f"sysbus.{s.bus}.{s.name}", "properties": props}
    chip: dict[str, Any] = {
        "path": chip_path(b, c),
        "access": "spi" if c.interface.protocol == "spi" else "bus",
        "framing": "forge-spi-apb" if c.interface.protocol == "spi" else "",
        "size": regs.size if regs else CHIP_SIZE_DEFAULT,
        "inputs": {name: i + (1 if c.interface.protocol == "spi" else 0) for i, name in enumerate(chip_inputs(c))},
        "outputs": {name: i for i, name in enumerate(chip_outputs(b, c))},
        "record_gpio": 100,
        "registers": {str(r.offset): r.name for r in regs.registers} if regs else {},
    }
    if c.interface.protocol != "spi":
        chip["base"] = int(p["bus_windows"][b.chip.interface]["base"])
    return {
        "autostart": autostart,
        "uarts": {"console": p["uarts"][b.console]["renode"]},
        "sensors": sensors,
        "chip": chip,
        "leds": leds_to_watch(b, c),
        "initial": {"sensors": initial},
        "analog": analog_session(b, c),
    }


def session_json_text(b: Board, c: Contract, regs: RegisterMap | None) -> str:
    return json.dumps(session_json(b, c, regs), indent=1)
