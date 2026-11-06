"""The board's schematic and PCB, made by Forge (step 4 and 5 of the flow).

Forge writes the thin parts itself and leaves the thick ones to established tools:

- ``netlist``     the carrier module's parts and nets, from hardware.yaml, system.yaml and the
                  component catalog (the chip, its decoupling, clock and reset, the sensors and
                  their pull-ups, the chip's LEDs, the connector to the MCU board)
- ``footprints``  land patterns (QFN, SOIC, 0603, oscillator, pin header), IPC-7351-style
- ``place``       placement on the module template (the connector on the left, each small part
                  by the pin it serves)
- ``dsn``/``ses`` the Specctra files the autorouter (Freerouting) reads and writes
- ``kicad``       the KiCad schematic, board and project files
- ``run``/``report`` what the board image runs: the KiCad files from the routed board, then
                  the summary of KiCad's ERC and DRC

Every module but ``netlist`` uses the standard library only: the board image runs them as
``forge_pcb``.
"""
