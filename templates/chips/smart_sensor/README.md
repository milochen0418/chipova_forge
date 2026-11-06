# Smart temperature sensor chip
Compares a temperature the MCU writes over SPI with a threshold (with hysteresis), counts alarms, and drives an IRQ pin and an alarm pin. Registers from SystemRDL (PeakRDL-regblock) behind an SPI-to-APB bridge; QFN-16.
