"""The chip as a Renode co-simulated peripheral: the C++ around its Verilator model.

:func:`render_sim_main` and :func:`render_cmake` write the two files a build job compiles in the
simulation image together with the chip's RTL into ``libVchip.so``, which Renode loads.

How Renode reaches the chip, by the contract's interface:

- ``apb3``: the chip's registers sit on the MCU's memory bus. Renode's own APB3 driver
  (integration library) runs each bus transaction on the RTL's APB3 pins.
- ``spi``: the chip sits on the MCU's SPI controller (``ForgeSpiChip`` on Renode's side). Each
  byte the controller sends arrives as a one-byte read at offset = the byte; this file drives
  the chip's ``sclk``/``mosi`` pins bit by bit (mode 0, ``spi_half_period`` chip clocks per half
  period) and answers with what its ``miso`` pin said. The chip select is input pin 0, driven
  by the MCU's GPIO line.

Both ways, the chip's output pins go back to Renode as GPIO signals (numbered from 0, Renode's
``cosimToRenodeSignalRange``) and Renode's GPIO signals drive its input pins (numbered from 0).
Renode keeps the model's time in step with its own; the chip's clock runs at ``frequency_hz``
of virtual time.

It also writes what happened, as JSON lines with the chip's own time (``FORGE_EVENTS``): each
bus transaction or SPI byte, each pin change; and, when asked (GPIO ``RECORD_GPIO``) or around
each transaction, VCD waveform windows (``FORGE_VCD``).
"""

from __future__ import annotations

import dataclasses
import re

# Renode -> chip GPIO number that asks for a waveform window (the chip's inputs use 0..N-1).
RECORD_GPIO = 100


@dataclasses.dataclass(frozen=True)
class Pin:
    name: str  # what Forge calls it
    port: str  # the verilated top's port
    bit: int = 0  # the bit of a multi-bit port
    index: int = 0  # its GPIO number on Renode's side


@dataclasses.dataclass(frozen=True)
class Apb3:
    psel: str
    penable: str
    pwrite: str
    paddr: str
    pwdata: str
    prdata: str
    pready: str
    pslverr: str = ""


@dataclasses.dataclass(frozen=True)
class Spi:
    sclk: str
    mosi: str
    miso: str
    cs_n: str
    half_period: int = 8  # chip clock cycles per half sclk period


@dataclasses.dataclass(frozen=True)
class CosimSpec:
    top: str
    sources: tuple[str, ...]
    clock: str
    reset: str
    reset_active_low: bool
    apb3: Apb3 | None = None
    spi: Spi | None = None
    inputs: tuple[Pin, ...] = ()  # for SPI, the chip select is input 0 (added by render)
    outputs: tuple[Pin, ...] = ()
    frequency_hz: int = 1_000_000
    record_edges: int = 20_000
    include_dirs: tuple[str, ...] = ()


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check(spec: CosimSpec) -> None:
    if (spec.apb3 is None) == (spec.spi is None):
        raise ValueError("a chip is reached either by APB3 or by SPI")
    names = [spec.top, spec.clock, spec.reset]
    if spec.apb3:
        names += [n for n in dataclasses.astuple(spec.apb3) if n]
    if spec.spi:
        names += [spec.spi.sclk, spec.spi.mosi, spec.spi.miso, spec.spi.cs_n]
        if not 2 <= spec.spi.half_period <= 1000:
            raise ValueError("SPI half period: 2..1000 chip clocks")
    names += [p.port for p in spec.inputs + spec.outputs]
    for n in names:
        if not _IDENT.match(n):
            raise ValueError(f"not a Verilog identifier: {n!r}")
    for p in spec.inputs + spec.outputs:
        if not 0 <= p.bit < 32:
            raise ValueError(f"pin {p.name}: bits 0..31 of a port")
        if p.index == RECORD_GPIO:
            raise ValueError(f"GPIO {RECORD_GPIO} is Forge's own")
    if not 1_000 <= spec.frequency_hz <= 100_000_000:
        raise ValueError("the chip's clock in the simulation: 1 kHz to 100 MHz")
    for s in spec.sources:
        if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_./\-]*\.(sv|v|svh|vh)$", s) or ".." in s:
            raise ValueError(f"not a source path: {s!r}")


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def all_inputs(spec: CosimSpec) -> tuple[Pin, ...]:
    """The chip's inputs Renode drives: for SPI the chip select first (GPIO 0), then the rest."""
    if spec.spi is None:
        return spec.inputs
    shifted = tuple(dataclasses.replace(p, index=i + 1) for i, p in enumerate(spec.inputs))
    return (Pin("chip_select", spec.spi.cs_n, 0, 0),) + shifted


def render_sim_main(spec: CosimSpec) -> str:
    _check(spec)
    half_ps = max(1, round(1e12 / spec.frequency_hz / 2))
    inputs = all_inputs(spec)
    n_out = len(spec.outputs)
    out_sample = "\n".join(f"    outs[{i}] = (top->{p.port} >> {p.bit}) & 1;" for i, p in enumerate(spec.outputs)) or "    // no output pins"
    out_names = ", ".join(f'"{_q(p.name)}"' for p in spec.outputs) or '""'
    in_cases = "\n".join(
        f"        case {p.index}: top->{p.port} = (top->{p.port} & ~(1u << {p.bit})) | ((v ? 1u : 0u) << {p.bit}); name = \"{_q(p.name)}\"; break;"
        for p in inputs
    )
    register = "\n".join(f"    a->registerInterrupt(&outs[{i}], {p.index});" for i, p in enumerate(spec.outputs))

    if spec.apb3 is not None:
        b = spec.apb3
        pslverr = f"    bus->pslverr = &top->{b.pslverr};\n" if b.pslverr else ""
        bus_setup = f"""    APB3* bus = new APB3();
    bus->pclk = &top->{spec.clock};
    bus->prst = &top->{spec.reset};
    bus->paddr = (uint8_t*)&top->{b.paddr};
    bus->psel = &top->{b.psel};
    bus->penable = &top->{b.penable};
    bus->pwrite = &top->{b.pwrite};
    bus->pwdata = (uint32_t*)&top->{b.pwdata};
    bus->pready = &top->{b.pready};
    bus->prdata = (uint32_t*)&top->{b.prdata};
{pslverr}    bus->evaluateModel = &eval;
    a->addBus(bus);"""
        spi_code = ""
        read_override = f"""    void readFromBus(int width, uint64_t addr) override {{
        record(64);
        try {{
            uint64_t value = targetInterfaces[0]->read(width, addr);
            communicationChannel->sendMain(Protocol(readRequest, addr, value));
            char buf[200];
            snprintf(buf, sizeof buf, "{{\\"kind\\": \\"bus\\", \\"op\\": \\"r\\", \\"addr\\": %llu, \\"value\\": %llu, \\"t\\": %.9f}}",
                     (unsigned long long)addr, (unsigned long long)value, now_s());
            event_line(buf);
        }} catch (const char* msg) {{
            log(LOG_LEVEL_ERROR, msg);
            communicationChannel->sendMain(Protocol(error, 0, 0));
        }}
    }}
    void writeToBus(int width, uint64_t addr, uint64_t value) override {{
        record(64);
        RenodeAgent::writeToBus(width, addr, value);
        char buf[200];
        snprintf(buf, sizeof buf, "{{\\"kind\\": \\"bus\\", \\"op\\": \\"w\\", \\"addr\\": %llu, \\"value\\": %llu, \\"t\\": %.9f}}",
                 (unsigned long long)addr, (unsigned long long)value, now_s());
        event_line(buf);
    }}"""
    else:
        s = spec.spi
        assert s is not None
        bus_setup = f"""    ClockOnly* bus = new ClockOnly();
    bus->clk = &top->{spec.clock};
    bus->rst = &top->{spec.reset};
    bus->evaluateModel = &eval;
    a->addBus(bus);"""
        spi_code = f"""
// The chip's clock and reset, without a memory bus (the registers are reached over SPI).
struct ClockOnly : public BaseTargetBus {{
    uint8_t* clk = nullptr;
    uint8_t* rst = nullptr;
    void tick(bool countEnable, uint64_t steps) override {{
        for (uint64_t i = 0; i < steps; i++) {{ *clk = 1; evaluateModel(); *clk = 0; evaluateModel(); }}
        if (countEnable) tickCounter += steps;
    }}
    void timeoutTick(uint8_t* signal, uint8_t expectedValue, int timeout) override {{
        do {{ tick(true, 1); timeout--; }} while (*signal != expectedValue && timeout > 0);
        if (timeout == 0) throw "Operation timeout";
    }}
    void reset() override {{
#ifdef RESET_ACTIVE_LOW
        *rst = 0; tick(true, 2); *rst = 1; tick(true, 2);
#else
        *rst = 1; tick(true, 2); *rst = 0; tick(true, 2);
#endif
    }}
    void write(int, uint64_t, uint64_t) override {{ throw "this chip is reached over SPI, not a memory bus"; }}
    uint64_t read(int, uint64_t) override {{ throw "this chip is reached over SPI, not a memory bus"; }}
protected:
    bool areSignalsConnected() override {{ return clk && rst; }}
}};

static const uint64_t SPI_HALF = {s.half_period};
"""
        read_override = f"""    // One SPI byte, mode 0: mosi set while sclk is low, miso sampled at the rising edge.
    uint8_t spiTransfer(uint8_t tx) {{
        uint8_t rx = 0;
        for (int i = 7; i >= 0; i--) {{
            top->{s.mosi} = (tx >> i) & 1;
            tick(true, SPI_HALF);
            rx = (uint8_t)((rx << 1) | (top->{s.miso} & 1));
            top->{s.sclk} = 1;
            tick(true, SPI_HALF);
            top->{s.sclk} = 0;
        }}
        tick(true, SPI_HALF);
        return rx;
    }}
    void readFromBus(int width, uint64_t addr) override {{
        if (width != 1 || addr > 0xFF) {{
            log(LOG_LEVEL_ERROR, "this chip is reached over SPI: one byte at a time");
            communicationChannel->sendMain(Protocol(error, 0, 0));
            return;
        }}
        record(64 * SPI_HALF);
        uint8_t rx = spiTransfer((uint8_t)addr);
        communicationChannel->sendMain(Protocol(readRequest, addr, rx));
        char buf[160];
        snprintf(buf, sizeof buf, "{{\\"kind\\": \\"spi\\", \\"tx\\": %u, \\"rx\\": %u, \\"t\\": %.9f}}", (unsigned)addr, (unsigned)rx, now_s());
        event_line(buf);
    }}
    void writeToBus(int width, uint64_t addr, uint64_t value) override {{
        log(LOG_LEVEL_ERROR, "this chip is reached over SPI, not a memory bus");
        communicationChannel->sendMain(Protocol(error, 0, 0));
    }}"""

    return f"""// Generated by Chipova Forge: the chip `{spec.top}` as a Renode co-simulated peripheral ({'APB3' if spec.apb3 else 'SPI'}).
// Don't edit: Forge writes it again from the hardware contract on every build.
#include <verilated.h>
#include <verilated_vcd_c.h>
#include "Vchip.h"
#include "src/renode_bus.h"
#include "src/buses/apb3.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>

static Vchip* top = nullptr;
static RenodeAgent* agent = nullptr;
static VerilatedVcdC* vcd = nullptr;
static FILE* events = nullptr;
static const int N_OUT = {n_out};
static uint8_t outs[N_OUT > 0 ? N_OUT : 1];
static uint8_t last_outs[N_OUT > 0 ? N_OUT : 1];
static const char* OUT_NAMES[] = {{{out_names}}};
static uint64_t edges = 0;          // clock edges so far (two per cycle)
static uint64_t record_until = 0;   // record the waveform up to this edge
static uint64_t last_dump = 0;
static bool dumped = false;
static uint8_t last_clk = 0;
static const uint64_t HALF_PERIOD_PS = {half_ps}ULL;
static const uint64_t RECORD_EDGES = {int(spec.record_edges)}ULL;

static double now_s() {{ return (double)edges * (double)HALF_PERIOD_PS * 1e-12; }}

static void event_line(const char* json) {{
    if (!events) return;
    fputs(json, events);
    fputc('\\n', events);
    fflush(events);
}}

static void eval() {{
    top->eval();
    if (top->{spec.clock} != last_clk) {{ last_clk = top->{spec.clock}; edges++; }}
    if (vcd) {{
        if (edges <= record_until) {{
            uint64_t t = edges * HALF_PERIOD_PS;
            if (!dumped || t > last_dump) {{ vcd->dump(t); last_dump = t; dumped = true; }}
        }} else if (record_until && edges == record_until + 1) {{
            vcd->flush();
        }}
    }}
{out_sample}
    for (int i = 0; i < N_OUT; i++) {{
        if (outs[i] != last_outs[i]) {{
            last_outs[i] = outs[i];
            char buf[256];
            snprintf(buf, sizeof buf, "{{\\"kind\\": \\"pin\\", \\"pin\\": \\"%s\\", \\"value\\": %d, \\"t\\": %.9f}}", OUT_NAMES[i], outs[i], now_s());
            event_line(buf);
        }}
    }}
    if (agent) agent->handleInterrupts();
}}

static void record(uint64_t n) {{
    if (record_until < edges + n) record_until = edges + n;
}}
{spi_code}
class ForgeAgent : public RenodeAgent {{
public:
{read_override}
    void handleCustomRequestType(Protocol* m) override {{
        if (m->actionId != interrupt) {{ RenodeAgent::handleCustomRequestType(m); return; }}
        int n = (int)m->addr;
        bool v = m->value != 0;
        if (n == {RECORD_GPIO}) {{
            if (v) {{
                record(RECORD_EDGES);
                char buf[160];
                snprintf(buf, sizeof buf, "{{\\"kind\\": \\"trace\\", \\"detail\\": \\"recording %llu cycles\\", \\"t\\": %.9f}}", (unsigned long long)(RECORD_EDGES / 2), now_s());
                event_line(buf);
            }}
            communicationChannel->sendMain(Protocol(ok, 0, 0));
            return;
        }}
        const char* name = nullptr;
        switch (n) {{
{in_cases}
        default: break;
        }}
        if (!name) {{
            log(LOG_LEVEL_WARNING, "the chip has no input pin %d", n);
            communicationChannel->sendMain(Protocol(ok, 0, 0));
            return;
        }}
        record(64);
        tick(true, 4);  // a few clocks, so the chip's input flip-flops see the change
        char buf[256];
        snprintf(buf, sizeof buf, "{{\\"kind\\": \\"pin\\", \\"pin\\": \\"%s\\", \\"value\\": %d, \\"t\\": %.9f, \\"input\\": true}}", name, v ? 1 : 0, now_s());
        event_line(buf);
        communicationChannel->sendMain(Protocol(ok, 0, 0));
    }}
}};

static void initBus(RenodeAgent* a) {{
{bus_setup}
{register}
}}

static ForgeAgent* makeAgent() {{
    static VerilatedContext context;
    Verilated::traceEverOn(true);
    static Vchip model(&context);
    top = &model;
    const char* vcd_path = getenv("FORGE_VCD");
    if (vcd_path && *vcd_path) {{
        vcd = new VerilatedVcdC;
        top->trace(vcd, 99);
        vcd->open(vcd_path);
    }}
    const char* events_path = getenv("FORGE_EVENTS");
    if (events_path && *events_path) events = fopen(events_path, "a");
    static ForgeAgent a;
    return &a;
}}

RenodeAgent* Init() {{
    ForgeAgent* a = makeAgent();
    a->connectNative();
    initBus(a);
    agent = a;
    a->reset();  // Renode doesn't reset a peripheral library when it loads it
    return a;
}}

int main(int argc, char** argv) {{
    if (argc < 3) {{ printf("Usage: %s {{receiverPort}} {{senderPort}} [{{address}}]\\n", argv[0]); return 1; }}
    ForgeAgent* a = makeAgent();
    a->connect(atoi(argv[1]), atoi(argv[2]), argc < 4 ? "127.0.0.1" : argv[3]);
    initBus(a);
    agent = a;
    a->simulate();
    top->final();
    if (vcd) vcd->close();
    return 0;
}}
"""


def render_cmake(spec: CosimSpec) -> str:
    _check(spec)
    sources = " ".join(spec.sources)
    incs = " ".join(f"-I{d}" for d in spec.include_dirs)
    reset_def = "target_compile_definitions(chip PRIVATE RESET_ACTIVE_LOW)" if spec.reset_active_low else ""
    return f"""# Generated by Chipova Forge: builds the chip `{spec.top}` into libVchip.so for Renode.
cmake_minimum_required(VERSION 3.13)
cmake_policy(SET CMP0144 NEW)
project(forge_chip CXX)
find_package(verilator REQUIRED HINTS $ENV{{VERILATOR_ROOT}})
set(COSIM_DIR $ENV{{RENODE_ROOT}}/plugins/IntegrationLibrary)
file(GLOB_RECURSE RENODE_SOURCES ${{COSIM_DIR}}/*.cpp)
list(REMOVE_ITEM RENODE_SOURCES ${{COSIM_DIR}}/src/buses/cfu.cpp ${{COSIM_DIR}}/src/renode_cfu.cpp)
add_library(chip SHARED sim_main.cpp ${{RENODE_SOURCES}})
target_include_directories(chip PRIVATE ${{COSIM_DIR}})
target_compile_options(chip PRIVATE -fPIC -O2 -Wno-unused -Wno-unused-variable)
target_link_options(chip PRIVATE -static-libstdc++ -static-libgcc)
set_target_properties(chip PROPERTIES OUTPUT_NAME Vchip)
{reset_def}
verilate(chip SOURCES {sources} TOP_MODULE {spec.top} PREFIX Vchip TRACE
         VERILATOR_ARGS -Wno-fatal -Wno-lint -Wno-style -O2 {incs})
"""
