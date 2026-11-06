# Smart temperature controller

An STM32F4 reads a TMP1075 temperature sensor every 100 ms and writes the temperature into the
smart sensor chip over SPI. The chip compares it with its threshold (with hysteresis), counts
the alarms, lights its alarm LED and raises an interrupt. The firmware answers the interrupt,
reports `ALARM ...` on its console and clears the chip's alarm.

## Files

| File | What it is |
|---|---|
| `hardware.yaml` | The chip's hardware contract: its interface, pins, clock and package (made from the chip) |
| `system.yaml` | The board: which MCU pins reach the chip, the sensor, the console and the LEDs |
| `device.yaml` | The device API: the console's commands and events, used by the dashboard, the REST API and the SDK |
| `firmware/main.c` | The firmware; the pins come from the generated `board.h` |
| `tests/*.yaml` | System tests: set the temperature, send commands, expect replies, events, LEDs and registers |

## Try it

1. Build the registers, the chip and the firmware (Overview).
2. Start the simulation (Lab), then drag the temperature above 30 °C: the red LED lights and
   the console shows `ALARM`.
3. Run the tests (Tests), then make a release (Release).

The simulation runs the chip's own RTL (Verilator) beside the firmware on a virtual STM32F4
(Renode). The chip's clock in the simulation is slower than on silicon; see the release's
README for what the models leave out.
