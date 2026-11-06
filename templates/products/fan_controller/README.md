# Enclosure fan controller

The smart sensor chip as a thermostat. An STM32F4 reads a TMP1075 every 100 ms and writes the
temperature into the chip over SPI. Above the threshold (30 °C) the chip raises its alarm and its
alarm pin switches the fan on; below the threshold minus the hysteresis (29.5 °C) the fan goes
off again.

The enclosure's temperature isn't set by hand here: it comes from the board's analog side, a
thermal circuit (templates/analog/thermal_fan.yaml) that ngspice solves in step with the board,
every 50 ms of virtual time. The electronics heat the enclosure, its case loses heat to the
ambient air, and the fan, switched by the chip's pin, cools it faster. So firmware, chip RTL and
physics close one loop: watch the chart on the Lab page, or change the ambient air and the load.

## Try it

1. Start the simulation (Lab): the enclosure warms from 28 °C; past 30 °C the console says
   `ALARM`, the fan comes on, the temperature falls, `NORMAL`, the fan goes off, and again.
2. Set the load to 0 W: the enclosure cools towards the ambient air and the fan stays off.
3. Run the test (Tests), lay out the module (Board): it carries the fan's MOSFET and connector.

The thermal model is lumped (one temperature, a fan fully on or off); see
templates/analog/thermal_fan.yaml for its numbers and what it leaves out.
