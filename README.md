# Chipova Forge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

From a finished chip to a working virtual product, together. Chipova Forge takes a chip
designed in [Chipova](https://github.com/milochen0418/chipova) (its RTL, pins and SystemRDL
registers) and builds everything around it: the hardware contract, the board, the firmware, a
simulation where the firmware runs on a virtual MCU while the chip's own RTL runs beside it,
the device's API and app, end-to-end tests, the module's schematic and PCB, and a release
package with its evidence. Everyone who opens a product works on the same one at the same time;
AI agents work through the AI Hub with fewer rights than people.

It is a Python [Reflex](https://reflex.dev) app. Forge writes the glue; the engines are
established open-source tools, each run in a container: Renode (the MCU), Verilator (the chip),
Arm GCC (the firmware), ngspice (the analog side), Freerouting (the PCB's routes) and KiCad
(ERC, DRC, Gerbers).

## The flow, step by step

| # | Step | What Forge does | Engine |
|---|------|-----------------|--------|
| 1 | Import the chip | Takes a Chipova release (a `.zip`, an item of the platform's `chips` collection, or an example) and checks every file against its snapshot id | — |
| 2 | Hardware contract | Elaborates the RTL's ports, finds the interface (SPI, APB3 or none), clock, reset, interrupts, package pins, electrical limits: `hardware.yaml` | pyslang |
| 3 | System architecture | `system.yaml`: the MCU platform, which pins reach the chip, the sensors, the console, the LEDs; checked for clashes | — |
| 4 | Circuit schematic | The carrier module's parts and nets, a KiCad schematic, ERC | KiCad |
| 5 | PCB | Placement on the module template, routing, ground pours and stitching, DRC with schematic parity, Gerbers, drill, BOM, positions | Freerouting, KiCad |
| 6 | Electrical simulation | An analog model (a SPICE circuit) solved in lock-step with the board, driving its sensors and driven by the chip's pins | ngspice (libngspice) |
| 7 | Firmware | Bare-metal C on the platform's start-up code, Forge's chip driver (SPI frames or the external bus), `board.h` and the register header | Arm GCC |
| 8 | Virtual chip | The chip's RTL as a co-simulation library (a register harness for chips with no bus) | Verilator |
| 9 | Co-simulation | The firmware on Renode's STM32F407 with the chip's RTL on SPI (pin level) or the external bus; sensors, LEDs, UART | Renode × Verilator |
| 10 | Device gateway | One protocol (`device.yaml`) for the simulated board and a real one: REST API, events, the Python SDK | — |
| 11 | The product's app | A dashboard built only on the device API | Reflex |
| 12 | Verification & release | System tests (`tests/*.yaml`) in fresh simulations; release checks; a package with every file's SHA-256, approved by someone else | — |

The registers come from one SystemRDL description (PeakRDL): the chip's CSR RTL is checked
against it, the firmware's C header and the SDK's Python map are made from it.

## Products to start from

- **Smart temperature controller**: an STM32F4 reads a TMP1075 and writes the temperature into
  the smart sensor chip over SPI; above its threshold the chip raises an interrupt and the
  firmware reports the alarm. Tests: the alarm, the device protocol.
- **Enclosure fan controller**: the same chip as a thermostat. Its alarm pin switches a fan;
  the enclosure's temperature comes from a thermal circuit ngspice solves in step with the
  board, so firmware, chip RTL and physics close one loop. Its module carries the fan's MOSFET.
- **Chip bring-up board**: any chip's registers from a console; a chip with no bus (the
  Heartbeat example) sits in Forge's register harness.

## Running it

Python 3.11 with Poetry (the venv is in-project), Docker, and Forge's two images:

```bash
poetry env use python3.11 && poetry install
docker build -t chipova-forge/sim:1   infra/dockerfiles/forge-sim     # Renode, Verilator, Arm GCC, ngspice
docker build -t chipova-forge/board:1 infra/dockerfiles/forge-board   # KiCad 9, Freerouting
poetry run ./reflex_rerun.sh
```

With the platform's download cache, add `--build-arg PKG_CACHE=http://host.docker.internal:3142`.
The script uses ports 3000 and 8000 and frees them by force; if another app holds them, run
`REFLEX_API_URL=http://localhost:8310 poetry run reflex run --frontend-port 3310 --backend-port 8310`.
Data lives in `FORGE_HOME` (default `~/Library/Application Support/chipova-forge` on macOS,
`~/.local/share/chipova-forge` on Linux).

Builds and simulations run in containers with no network, as user 1000, without capabilities,
with CPU, memory and process limits. The app copies their inputs in and their outputs out; it
never mounts host folders into them.

## In the Agentic App Store

The catalog entry `chipova-forge` gives the app the Docker socket, a data volume and read access to the
`chips` collection. Install it from the store; until the GitHub repository exists, use Dev mode
with this folder. People sign in through the platform; the AI Hub's assistant opens on a
product with the AI button, and its `forge.*` tools (22) read, build, simulate, test and package.
An AI never edits a file (it proposes; a person accepts or rejects), never decides on proposals
and never approves releases; every call is in the project's audit log.

## For scripts: the REST API and the SDK

Make a token on a product's AI & API page; calls with it are yours. `sdk/` is the Python SDK:

```python
from chipova_forge_sdk import Device
device = Device("ses-…", url="https://chipova-forge.reflex-ddns.com", token="cfg1.…")
print(device.get_status().temperature)
device.set_threshold(celsius=35)
device.environment("temperature", celsius=40)   # the virtual board only
device.environment("analog", load=0)            # a board with an analog side
real = Device.serial("/dev/cu.usbmodem1101", profile="device.yaml")  # the same calls, a real board
```

## Tests

```bash
poetry run pytest -m "not docker"     # seconds: contracts, registers, boards, PCB files, permissions
poetry run pytest -m docker           # about two minutes: real builds, simulations, tests, PCBs, the analog loop
FRONTEND_PORT=3320 BACKEND_PORT=8320 poetry run ./run_test_suite.sh smoke_home   # see testcases/README.md
```

## What the models leave out

A passing simulation means the digital design and its firmware work in simulation; it is not a
hardware qualification or a foundry signoff. The chip's clock in simulation is far slower than
on silicon (1 MHz by default); the SPI and sensor models are behavioural; the analog models are
lumped; the PCB is a two-layer carrier module checked by KiCad's rules, not by a fab's own
rules or a signal-integrity tool. `docs/status.md` lists what is done and what isn't, and each
release's README says what its evidence covers.

## More

- [docs/architecture.md](docs/architecture.md): how it is built
- [docs/status.md](docs/status.md): what is done, what isn't, known limits
- [testcases/README.md](testcases/README.md): the browser suites
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md): the tools Forge runs and their licences

## License

Chipova Forge is released under the [MIT License](LICENSE): you may use, copy, change, merge,
publish, distribute, sublicense and sell it, in your own projects and products, as long as the
copyright notice and the licence text stay with it. It comes without warranty.

Copyright (c) 2026 Milo Chen.

The tools Forge runs in its container images (Renode, Verilator, Arm GCC, ngspice, KiCad,
Freerouting, OpenJDK) keep their own licences, some of them GPL. Forge runs them as separate
programs and doesn't link them into its code; [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
lists each one. Images you build and share carry those programs, so their licences apply to them.
