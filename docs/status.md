# Status

What Chipova Forge does today (2026-11-06), what it doesn't, and what was checked.

## Done

- **Import**: Chipova release zips (each file checked against the snapshot id), items of the
  `chips` collection, examples (smart sensor: SPI; Heartbeat: no bus).
- **Contract**: ports from the elaborated RTL, interface, clock, reset, interrupts, package pins,
  electrical limits; checks.
- **Registers**: PeakRDL CSR RTL, C header, Python map; the chip's CSR RTL checked against them.
- **Board**: `system.yaml` on the STM32F4 Discovery platform; pin clash checks; `board.h`;
  Renode's board; a picture.
- **Firmware**: Arm GCC builds with Forge's SPI or bus driver; problems with file and line.
- **Co-simulation**: Renode × Verilator, APB3 on the external bus or SPI at pin level, register
  probes, waveforms, sensors, LEDs, UART; several people on one board.
- **Analog side**: ngspice's library in lock-step with Renode (50 ms steps); the enclosure fan
  controller closes firmware → chip → fan → temperature → sensor.
- **Device API**: one protocol for the simulated and a real board; REST API with tokens, events
  (SSE), the Python SDK (virtual or serial).
- **App**: a dashboard built on the device API; its controls are device.yaml's dashboard controls (a new
  one there is a new control here, no code); a reply to anyone's command (a person's, the AI's, a
  script's) updates everyone's tiles, and older events can't undo it.
- **Tests and release**: system tests in fresh simulations; release checks (chip, contract,
  board, registers, builds, tests, schematic & PCB); packages with SHA-256 manifests; approval
  by someone else.
- **Schematic and PCB**: a two-layer carrier module per product, routed by Freerouting, ERC and
  DRC clean with the board matching the schematic (all three products), Gerbers, drill, BOM,
  positions, KiCad files, pictures.
- **AI**: 22 `forge.*` tools for the AI Hub; proposals that people decide, as whole texts or, for a
  small change in a long file, as edits (old → new, tolerant of indentation and of the names coding
  tools use); a newer proposal by the same AI for the same files replaces its pending one; the audit log.
  What it reads: the product's files and the outputs the page shows (a PCB build's netlist, parts
  list, placement, ERC/DRC; any build's log) at once; the chip's sources and other outputs only after
  the person allows it in the AI Hub's chat (`forge.read_output`, `reflex_ddns_auth.tools.ask_first`).
- **People**: roles, the link's access, members; an owner can delete the product (its simulations stop).
- **Tools**: when its two images aren't on the computer (an App Store install doesn't bring them), the home page
  builds them in the app, with progress.
- **AI proposals** show as a diff: only the changed lines and three around them, numbered, with the AI's reason.

## Checked

- 38 unit tests, 5 container tests (builds, a live session, both smart-sensor system tests, a
  release approved by a reviewer, the Heartbeat in its harness, both PCBs, the analog loop, building a tool image).
- Browser suites: `smoke_home`, `lab_flow`, `release_review`, `ai_api`, `analog_loop`.

## Not done, or limited

- **Chipova publishing into `chips`**: Forge reads the collection, Chipova doesn't write to it
  yet (upload the release zip instead).
- **Platforms**: one, the STM32F4 Discovery (Renode's STM32F407). The SPI and I²C models are
  Renode's; the chip's clock in simulation is 1 MHz by default.
- **PCB**: QFN chips with an SPI interface or no bus; APB3 chips on the MCU's parallel bus are not
  laid out. Two layers, a fixed module template, Forge's own land patterns (check the
  oscillator's against its datasheet), KiCad's default rules plus Forge's, no fab-specific rules,
  no 3D models, no impedance or signal-integrity analysis.
- **Analog**: one model (a lumped thermal enclosure with a fan); drives must be chip pins shown
  as LEDs; ngspice stops at the first time point after each step (at most one solver step late).
- **Sensors**: a TMP1075/TMP108 temperature register model; no conversion time, noise or alert pin.
- **App Store**: the catalog entry is there; until the repository is on GitHub, install it in
  Dev mode from this folder.
