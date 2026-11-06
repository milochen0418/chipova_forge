# Third-party notices

Chipova Forge (MIT) runs these programs in its container images, as separate programs; it does
not link them into its own code. Their licences apply to them.

| Program | Version | Licence | Where | What for |
|---|---|---|---|---|
| Renode | 1.17.0 | MIT | `forge-sim` (portable build) | The MCU, its peripherals, virtual time |
| Renode IntegrationLibrary | 1.17.0 | MIT | `forge-sim`, built into each chip's library | The chip's co-simulation (one call patched to compile) |
| Verilator | 5.032 (Debian) | LGPL-3.0 or Artistic-2.0 | `forge-sim` | The chip's RTL as a C++ model |
| Arm GNU Toolchain (gcc-arm-none-eabi), newlib | Debian trixie | GPL-3.0 with runtime exception; newlib BSD-style | `forge-sim` | The firmware |
| ngspice, libngspice | 44.2 (Debian) | Modified BSD (some parts LGPL/MPL/GPL as listed by ngspice) | `forge-sim` | The analog side |
| STM32F40x SVD | ST | ST's licence (as shipped by Renode's SVD repository) | `forge-sim` | Register names in Renode's logs |
| KiCad (kicad-cli, pcbnew module) | 9.0.8 (Debian trixie-backports) | GPL-3.0-or-later | `forge-board` | ERC, DRC, filling copper pours, Gerbers, BOM, pictures |
| Freerouting | 2.5.0 (jar) | GPL-3.0 | `forge-board` | Routing the module's PCB |
| OpenJDK | 25 (Debian) | GPL-2.0 with Classpath exception | `forge-board` | Runs Freerouting |

Python packages the app uses (from PyPI): Reflex (Apache-2.0), reflex-monaco (Apache-2.0),
pydantic (MIT), PyYAML (MIT), docker (Apache-2.0), PyJWT (MIT), systemrdl-compiler (MIT),
PeakRDL-regblock, PeakRDL-cheader, PeakRDL-python (LGPL-3.0, used as libraries unmodified;
PeakRDL-regblock's generated RTL carries its own notice), pyslang (MIT).
