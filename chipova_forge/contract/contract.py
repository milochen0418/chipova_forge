"""The Hardware Design Contract (``hardware.yaml``): what Forge knows about a chip, in one place.

Forge never guesses a chip's pins, bus or registers from its RTL alone each time it needs them:
on import it reads them once (the RTL elaborated, ``chipova.toml``, the package plan, the
SystemRDL registers, the electrical limits), writes them down here, and checks them against
each other. The board, the firmware, the co-simulation and the device API are all made from
this file; a person can correct it (e.g. a pin's role), and it is checked again.

    schema_version: "1.0"
    chip:      name, top module, the Chipova snapshot it was made from
    design:    the RTL files in order, the SystemRDL file, the module its registers live in
    clock:     the clock port, its real frequency and the frequency simulated
    reset:     the reset port and its polarity
    interface: how the MCU reaches the registers: spi (four pins) | apb3 (a memory bus) | none
    pins:      every port with its role (interface, irq, output, input, clock, reset)
    package:   the package and the pin map (package/pins.csv)
    electrical: supply, I/O levels, clock and SPI limits (electrical/io-spec.yaml)
    simulation: the engine and how fast the chip's clock runs in it

What Forge does with each interface:
    spi   the board wires the MCU's SPI pins and a GPIO (chip select) to the chip; in the
          simulation the MCU's SPI controller talks to the chip's real RTL pin by pin
    apb3  the chip sits on the MCU's external bus (a memory window); simulated at the
          transaction level
    none  the chip has no bus: Forge puts a small register harness around it (see harness.py)
"""

from __future__ import annotations

import csv
import io
import re
import time
import tomllib
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from chipova_forge.contract.chip_package import ChipPackage
from chipova_forge.contract.ports import Elaboration, Port, elaborate

SCHEMA_VERSION = "1.0"
SIM_FREQUENCY_HZ = 1_000_000


class Issue(BaseModel):
    level: Literal["error", "warning", "info"]
    where: str
    message: str
    fix: str = ""


class ChipInfo(BaseModel):
    name: str
    top_module: str
    snapshot: str
    source: str = ""
    imported_at: float = 0.0


class Design(BaseModel):
    rtl_sources: list[str]
    registers: str | None = None  # the SystemRDL file
    csr_module: str | None = None  # the module PeakRDL-regblock generates from it
    csr_rtl: str | None = None  # the chip's RTL file that holds that module


class Clock(BaseModel):
    port: str
    frequency_hz: int = 25_000_000


class Reset(BaseModel):
    port: str
    active_low: bool = True


class Interface(BaseModel):
    protocol: Literal["spi", "apb3", "none"]
    ports: dict[str, str] = Field(default_factory=dict)  # role -> port
    mode: int = 0  # SPI mode
    address_width: int = 8
    data_width: int = 32
    max_sclk_hz: int = 0


class PinUse(BaseModel):
    port: str
    direction: Literal["in", "out", "inout"]
    width: int = 1
    role: Literal["clock", "reset", "interface", "irq", "output", "input"]


class PinMapEntry(BaseModel):
    pin: int
    signal: str = ""
    kind: str = "nc"
    pad: str = ""


class Package(BaseModel):
    type: str = ""
    pins: int = 0
    body_mm: list[float] = Field(default_factory=list)
    pitch_mm: float = 0.0
    exposed_pad: str = ""
    pinmap: list[PinMapEntry] = Field(default_factory=list)


class Electrical(BaseModel):
    vdd: float = 3.3
    vdd_min: float = 3.0
    vdd_max: float = 3.6
    io_voltage: float = 3.3
    current_max_ma: float = 0.0
    max_clock_hz: int = 0
    source: str = "default"  # electrical/io-spec.yaml, or default


class Simulation(BaseModel):
    engine: Literal["verilator"] = "verilator"
    frequency_hz: int = SIM_FREQUENCY_HZ
    spi_half_period_cycles: int = 8


class Contract(BaseModel):
    schema_version: str = SCHEMA_VERSION
    chip: ChipInfo
    design: Design
    clock: Clock
    reset: Reset
    interface: Interface
    pins: list[PinUse]
    package: Package | None = None
    electrical: Electrical = Field(default_factory=Electrical)
    simulation: Simulation = Field(default_factory=Simulation)

    def port(self, name: str) -> PinUse | None:
        return next((p for p in self.pins if p.port == name), None)

    @property
    def irqs(self) -> list[PinUse]:
        return [p for p in self.pins if p.role == "irq"]

    @property
    def outputs(self) -> list[PinUse]:
        return [p for p in self.pins if p.role == "output"]

    @property
    def inputs(self) -> list[PinUse]:
        return [p for p in self.pins if p.role == "input"]


# ------------------------------------------------------------------ recognising ports

_RESET_LOW = re.compile(r"^(rst_?n|reset_?n|n_?rst|n_?reset|rstn_i|rst_ni|resetn)$", re.I)
_RESET_HIGH = re.compile(r"^(rst|reset|rst_i|reset_i|arst)$", re.I)
_CLOCK = re.compile(r"^(clk|clock|clk_i|clk_in|sys_clk|pclk)$", re.I)
_IRQ = re.compile(r"(^|_)(irq|intr|int|interrupt)(_?n|_o)?$|^irq", re.I)
_SPI = {
    "sclk": re.compile(r"(^|_)(sclk|sck|spi_?clk)$", re.I),
    "mosi": re.compile(r"(^|_)(mosi|sdi|copi|si)$", re.I),
    "miso": re.compile(r"(^|_)(miso|sdo|cipo|so)$", re.I),
    "cs_n": re.compile(r"(^|_)(cs_?n|ss_?n|n_?cs|n_?ss|cs|ss|csb)$", re.I),
}
_APB = ("psel", "penable", "pwrite", "paddr", "pwdata", "prdata", "pready", "pslverr")


def _find_apb(ports: list[Port]) -> dict[str, str]:
    """The APB3 slave ports, whatever their common prefix (psel, s_apb_psel, apb_psel, ...)."""
    names = {p.name.lower(): p.name for p in ports}
    for prefix in sorted({n[: -len("psel")] for n in names if n.endswith("psel")}, key=len):
        found = {role: names[prefix + role] for role in _APB if prefix + role in names}
        if all(r in found for r in _APB[:7]):
            return found
    return {}


def _find_spi(ports: list[Port]) -> dict[str, str]:
    found: dict[str, str] = {}
    for role, pat in _SPI.items():
        want = "out" if role == "miso" else "in"
        cands = [p for p in ports if p.direction == want and p.width == 1 and pat.search(p.name)]
        if cands:
            found[role] = cands[0].name
    return found if len(found) == 4 else {}


# ------------------------------------------------------------------ making the contract


def _read_pinmap(text: str) -> list[PinMapEntry]:
    rows = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    out = []
    for r in csv.DictReader(io.StringIO("\n".join(rows))):
        try:
            out.append(PinMapEntry(pin=int(r.get("pin", "0")), signal=(r.get("signal") or "").strip(),
                                   kind=(r.get("kind") or "nc").strip().lower(), pad=(r.get("pad") or "").strip()))
        except ValueError:
            continue
    return out


def _electrical(pkg: ChipPackage) -> Electrical:
    path = "electrical/io-spec.yaml"
    if path not in pkg.files:
        return Electrical()
    try:
        spec = yaml.safe_load(pkg.text(path)) or {}
    except yaml.YAMLError:
        return Electrical()
    supply, io_ = spec.get("supply") or {}, spec.get("io") or {}
    return Electrical(
        vdd=float(supply.get("vdd_typ", 3.3)),
        vdd_min=float(supply.get("vdd_min", 3.0)),
        vdd_max=float(supply.get("vdd_max", 3.6)),
        io_voltage=float(io_.get("voltage", 3.3)),
        current_max_ma=float(supply.get("current_max_ma", 0.0)),
        max_clock_hz=int((spec.get("clock") or {}).get("max_hz", 0)),
        source=path,
    )


def generate(pkg: ChipPackage) -> tuple[Contract | None, list[Issue], Elaboration | None]:
    """The contract of a chip package, and what is wrong or worth knowing about it."""
    issues: list[Issue] = []
    settings = pkg.settings()
    design = settings.get("design") or {}
    top = str(design.get("top") or "")
    rtl = [str(p) for p in design.get("rtl") or []]
    if not top:
        issues.append(Issue(level="error", where="chipova.toml", message="the top module isn't set ([design] top)"))
    missing = [p for p in rtl if p not in pkg.files]
    for p in missing:
        issues.append(Issue(level="error", where="chipova.toml", message=f"the RTL file {p} isn't in the chip"))
    if not rtl:
        issues.append(Issue(level="error", where="chipova.toml", message="no RTL files are listed ([design] rtl)"))
    if not top or not rtl or missing:
        return None, issues, None

    elab = elaborate({p: pkg.text(p) for p in rtl}, top)
    for e in elab.errors:
        issues.append(Issue(level="error", where="RTL", message=e))
    if not elab.ports:
        return None, issues, elab
    ports = elab.ports

    # Clock and reset
    clock_name = str(design.get("clock_port") or "")
    clock = next((p for p in ports if p.name == clock_name), None) or next((p for p in ports if _CLOCK.match(p.name)), None)
    period = float(design.get("clock_period_ns") or 0)
    freq = int(round(1e9 / period)) if period > 0 else 25_000_000
    reset = next((p for p in ports if p.direction == "in" and (_RESET_LOW.match(p.name) or _RESET_HIGH.match(p.name))), None)
    if clock is None:
        issues.append(Issue(level="error", where="ports", message="no clock port found", fix="set [design] clock_port in chipova.toml"))
    if reset is None:
        issues.append(Issue(level="error", where="ports", message="no reset port found (rst_n, nreset, rst, reset ...)"))
    if clock is None or reset is None:
        return None, issues, elab

    # The interface the MCU uses
    spi = _find_spi(ports)
    apb = _find_apb(ports)
    if spi:
        iface = Interface(protocol="spi", ports=spi, mode=0, address_width=7, data_width=32)
    elif apb:
        paddr = next(p for p in ports if p.name == apb["paddr"])
        iface = Interface(protocol="apb3", ports=apb, address_width=paddr.width, data_width=32)
        if paddr.width > 8:
            issues.append(Issue(level="warning", where="interface", message=f"{apb['paddr']} is {paddr.width} bits; the simulation reaches the first 256 bytes"))
    else:
        iface = Interface(protocol="none")
        issues.append(Issue(level="info", where="interface", message="no SPI or APB3 port found: Forge will wrap the chip in a register harness (the MCU reaches its pins through registers)"))

    used = {clock.name, reset.name, *iface.ports.values()}
    pins: list[PinUse] = [
        PinUse(port=clock.name, direction="in", width=1, role="clock"),
        PinUse(port=reset.name, direction="in", width=1, role="reset"),
    ]
    for p in ports:
        if p.name in used:
            if p.name not in (clock.name, reset.name):
                pins.append(PinUse(port=p.name, direction=p.direction, width=p.width, role="interface"))
            continue
        if p.direction == "out":
            role = "irq" if _IRQ.search(p.name) and p.width == 1 else "output"
        elif p.direction == "in":
            role = "input"
        else:
            role = "output"
        pins.append(PinUse(port=p.name, direction=p.direction, width=p.width, role=role))
        if p.width > 32:
            issues.append(Issue(level="warning", where="ports", message=f"{p.name} is {p.width} bits wide: the board sees its first 32"))

    # Registers
    rdl = next((p for p in sorted(pkg.files) if p.startswith("registers/") and p.endswith(".rdl")), None)
    csr_module = csr_rtl = None
    if rdl:
        m = re.search(r"\baddrmap\s+([A-Za-z_][A-Za-z0-9_]*)", pkg.text(rdl))
        if m:
            csr_module = m.group(1)
            for p in rtl:
                if re.search(rf"\bmodule\s+{re.escape(csr_module)}\b", pkg.text(p)):
                    csr_rtl = p
                    break
    elif iface.protocol != "none":
        issues.append(Issue(level="warning", where="registers", message="the chip has no SystemRDL description (registers/*.rdl): no C header or register map for the firmware and the SDK"))

    # Package
    package = None
    if "package/package.toml" in pkg.files:
        try:
            pk = (tomllib.loads(pkg.text("package/package.toml")).get("package")) or {}
        except tomllib.TOMLDecodeError:
            pk = {}
            issues.append(Issue(level="warning", where="package/package.toml", message="doesn't read"))
        pinmap = _read_pinmap(pkg.text("package/pins.csv")) if "package/pins.csv" in pkg.files else []
        package = Package(type=str(pk.get("type", "")), pins=int(pk.get("pins", 0) or 0), body_mm=[float(x) for x in pk.get("body_mm", [])],
                          pitch_mm=float(pk.get("pitch_mm", 0) or 0), exposed_pad=str(pk.get("exposed_pad", "")), pinmap=pinmap)
    else:
        issues.append(Issue(level="warning", where="package", message="the chip has no package plan (package/package.toml, pins.csv): the board can't place it"))

    contract = Contract(
        chip=ChipInfo(name=str(pkg.source.get("name") or top), top_module=top, snapshot=pkg.snapshot,
                      source=str(pkg.source.get("ref", "")), imported_at=round(time.time(), 3)),
        design=Design(rtl_sources=rtl, registers=rdl, csr_module=csr_module, csr_rtl=csr_rtl),
        clock=Clock(port=clock.name, frequency_hz=freq),
        reset=Reset(port=reset.name, active_low=bool(_RESET_LOW.match(reset.name))),
        interface=iface,
        pins=pins,
        package=package,
        electrical=_electrical(pkg),
        simulation=Simulation(),
    )
    if iface.protocol == "spi":
        spi_limit = 0
        if "electrical/io-spec.yaml" in pkg.files:
            try:
                spi_limit = int(((yaml.safe_load(pkg.text("electrical/io-spec.yaml")) or {}).get("spi") or {}).get("max_sclk_hz", 0))
            except (yaml.YAMLError, ValueError, TypeError):
                spi_limit = 0
        contract.interface.max_sclk_hz = spi_limit or freq // 8
    issues.extend(check(contract))
    return contract, issues, elab


# ------------------------------------------------------------------ checking it


def check(c: Contract) -> list[Issue]:
    """What doesn't fit together in a contract (also after a person edited it)."""
    issues: list[Issue] = []
    names = {p.port for p in c.pins}
    for role, port in c.interface.ports.items():
        if port not in names:
            issues.append(Issue(level="error", where="interface", message=f"the {role} port {port!r} isn't a port of the chip"))
    if c.interface.protocol == "spi" and set(c.interface.ports) != {"sclk", "mosi", "miso", "cs_n"}:
        issues.append(Issue(level="error", where="interface", message="SPI needs sclk, mosi, miso and cs_n"))
    if c.interface.protocol == "apb3" and not all(r in c.interface.ports for r in _APB[:7]):
        issues.append(Issue(level="error", where="interface", message="APB3 needs psel, penable, pwrite, paddr, pwdata, prdata, pready"))
    if not 1_000 <= c.simulation.frequency_hz <= 100_000_000:
        issues.append(Issue(level="error", where="simulation", message="the simulated clock is 1 kHz .. 100 MHz"))

    if c.package is not None and c.package.pinmap:
        pm = c.package.pinmap
        by_signal: dict[str, list[int]] = {}
        for e in pm:
            if e.signal and e.kind not in ("power", "ground", "nc"):
                by_signal.setdefault(e.signal, []).append(e.pin)
            if c.package.pins and not 1 <= e.pin <= c.package.pins:
                issues.append(Issue(level="error", where="package/pins.csv", message=f"pin {e.pin} isn't on a {c.package.pins}-pin package"))
        for p in c.pins:
            bits = [p.port] if p.width == 1 else [f"{p.port}[{i}]" for i in range(p.width)]
            for b in bits:
                hits = by_signal.get(b, [])
                if not hits and p.width > 1 and p.port in by_signal:
                    continue  # one pin row for the whole port
                if not hits:
                    issues.append(Issue(level="error", where="package/pins.csv", message=f"{b} has no pin", fix="give it a pin, or say why in the package plan"))
                elif len(hits) > 1:
                    issues.append(Issue(level="error", where="package/pins.csv", message=f"{b} is on pins {', '.join(map(str, hits))}"))
        known = {p.port for p in c.pins} | {f"{p.port}[{i}]" for p in c.pins for i in range(p.width)}
        for sig in by_signal:
            if sig not in known:
                issues.append(Issue(level="error", where="package/pins.csv", message=f"{sig!r} isn't a port of the chip"))
        if not any(e.kind == "power" for e in pm):
            issues.append(Issue(level="error", where="package/pins.csv", message="no power pin"))
        if not any(e.kind == "ground" for e in pm) and not c.package.exposed_pad:
            issues.append(Issue(level="error", where="package/pins.csv", message="no ground pin"))
    if c.electrical.max_clock_hz and c.clock.frequency_hz > c.electrical.max_clock_hz:
        issues.append(Issue(level="warning", where="clock", message=f"chipova.toml's clock ({c.clock.frequency_hz} Hz) is above the data sheet's limit ({c.electrical.max_clock_hz} Hz)"))
    return issues


def to_yaml(c: Contract) -> str:
    head = (
        "# Chipova Forge: the Hardware Design Contract of this project's chip.\n"
        "# Made from the chip's files when it was imported; the board, the firmware, the simulation\n"
        "# and the device API are made from it. You may correct it (a pin's role, the simulated clock):\n"
        "# Forge checks it again on every build.\n"
    )
    return head + yaml.safe_dump(c.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100)


def from_yaml(text: str) -> Contract:
    data: Any = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("hardware.yaml isn't a mapping")
    return Contract.model_validate(data)
