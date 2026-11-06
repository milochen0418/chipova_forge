# Architecture

Chipova Forge is one Reflex app (Python 3.11) plus two container images. The app keeps the
projects, runs nothing heavy itself, and talks to Docker through its socket.

```
browser ── Reflex (pages, shared rooms) ──┬── services: projects, proposals, releases, tests, audit (SQLite)
AI Hub ─── forge.* tools ─────────────────┤
scripts ── REST API (cfg1 tokens) ────────┤
                                          ├── jobs/builds ── job containers (forge-sim | forge-board)
                                          └── sessions ───── session containers (forge-sim): agent ⇄ Renode ⇄ Verilator, ngspice
```

## The code

| Package | What it holds |
|---|---|
| `contract/` | The chip package (Chipova snapshot ids, release zips, the lock), the elaborated ports (pyslang), the hardware contract, the registers (PeakRDL) and their check against the chip's CSR RTL |
| `system/` | The platform and component catalogs, `system.yaml` (the board, its checks, `board.h`, Renode's description, the session's configuration, the analog side), the board picture |
| `cosim/` | The co-simulation wrapper Forge generates for each chip (Verilator + Renode's IntegrationLibrary: APB3 bus or pin-level SPI), the register harness for chips with no bus, VCD and waveform and analog charts |
| `pcb/` | The carrier module: netlist, footprints, placement, the autorouter's files (DSN/SES), the KiCad files, ground stitching, and what the board image runs |
| `jobs/` | Builds: plans (inputs, script, image), a queue, containers, manifests with SHA-256 of every input and output, reuse of identical builds |
| `sessions/` | Simulation sessions: containers, the channel to the agent, the app's view of a running board; `agent/` runs inside the container |
| `gateway/` | The device protocol (`device.yaml`) and the REST API |
| `services/` | Projects, files, roles, proposals, system tests, releases, signed downloads, presence, the audit log |
| `agents/` | The AI Hub's tools |
| `states/`, `components/` | The pages: a shared room per product (everyone sees the same board), each tab's own state |

## Builds

A build is a plan: the files it reads, a fixed script, an image. Its inputs' hash (with the
image id and the generator's version) names it: asking again for the same inputs reuses the
earlier build. Kinds:

- `registers` (in-process): PeakRDL's CSR RTL, C header and Python map; the chip's CSR RTL is
  compared with what the SystemRDL generates.
- `chip` (`forge-sim`): Verilator builds the chip, or the chip inside the register harness, with
  Forge's wrapper into `libVchip.so`.
- `firmware` (`forge-sim`): Arm GCC builds the platform's start-up code, Forge's chip driver,
  `board.h`, the register header and `firmware/*.c`.
- `pcb` (`forge-board`): Freerouting routes the placed module; Forge writes the KiCad files;
  KiCad fills the pours, runs ERC and DRC (with schematic parity) and makes the outputs.

Job containers have no network, run as user 1000 with no capabilities, and have CPU, memory,
process and time limits. Their inputs are copied in and their outputs copied out.

## Sessions

A session container runs `sessions/agent/session_agent.py`. The agent starts Renode headless,
loads the board Forge described (`session.resc`, `board.repl`, the firmware, the chip's library),
and follows what happens into one numbered event log: UART lines and LED changes (written by
`hooks.py` inside Renode), the chip's bus transactions and pins (written by the chip's library),
and its own state. The app talks to it through `docker exec` of `ctl.py`: JSON requests, named
operations whose arguments are checked against the session's configuration; nothing from outside
reaches Renode's monitor.

A board with an analog side runs in lock-step: Renode runs one step of virtual time (50 ms by
default), ngspice (its shared library, through ctypes) solves the circuit on to the same time,
and at each step boundary the chip pins that drive the circuit go to it and its outputs go to
the board's sensors.

## The chip on the board

- **APB3 chips** sit on the MCU's external bus (FSMC bank 1, NE2–NE4): Renode's
  `CoSimulatedPeripheral` forwards bus accesses to the chip's Verilator model.
- **SPI chips** are wired at pin level: `ForgeSpiChip` (C#, compiled by Renode at start) turns
  each SPI byte into a transfer that toggles SCLK/MOSI/CS in the chip's RTL and samples MISO;
  the chip select comes from the MCU's GPIO.
- **Chips with no bus** sit in Forge's register harness (its own SystemRDL block): it holds the
  chip's reset and reads its outputs and their edges.

## The module's PCB

`pcb/netlist.py` makes the parts and nets from `hardware.yaml`, `system.yaml` and the component
catalog: the chip (QFN, its exposed pad with thermal vias), decoupling, a clock oscillator, a
power-on reset, the sensors with their pull-ups and address pins, LEDs on the chip outputs
`system.yaml` names, the analog side's drives (a fan's MOSFET, flyback diode and connector), and
J1 to the MCU board, its silkscreen naming the MCU pin of each signal. Placement puts J1 on the
left, the chip beside it, each small part by the pin it serves, the sensors on the right; then
Freerouting routes it, ground vias stitch the two pours, and KiCad checks it.

## Who may do what

Roles per project (owner, editor, reviewer, viewer) and the link's access (members only,
viewers, editors). An AI works with its person's access, minus editing files, deciding proposals,
approving releases and managing the project. A release is approved by someone other than the
person who made it.
