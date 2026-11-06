# Chip bring-up board

The MCU, the chip and a console: read and write any of the chip's registers from the console,
the device API or the SDK, and watch the chip's outputs on LEDs.

A chip with an APB3 interface sits on the MCU's external bus (FSMC). A chip with no bus, like
the Heartbeat example, sits inside Forge's register harness: a small register block that holds
the chip's reset, reads its outputs and counts their edges.

## Console commands

| Command | Reply |
|---|---|
| `PING` | `PONG` |
| `INFO` | the chip and firmware names |
| `READ <offset>` | `VALUE offset=... value=...` |
| `WRITE <offset> <value>` | `OK offset=... value=...` |

Offsets are multiples of 4 up to 252; offsets and values are decimal or `0x` hex. A refusal
is `ERR ...`.
