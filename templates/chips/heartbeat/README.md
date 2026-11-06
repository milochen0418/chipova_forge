# Heartbeat (Chipova's first template)
A counter that pulses its `out` pin once every 256 clocks. It has no bus: Forge wraps it in a small register harness so the MCU can reset it, read its pin and count its pulses.
