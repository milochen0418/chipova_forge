/* Chipova Forge: the firmware's way to the chip's registers, the same whichever way the board
 * wires it (SPI or the external memory bus). board.h says which; the build compiles the matching
 * forge_chip_spi.c or forge_chip_bus.c. Offsets are the register offsets of the chip's SystemRDL
 * (the C header's offsetof(<regs>_t, REG)). */
#ifndef FORGE_CHIP_H
#define FORGE_CHIP_H
#include <stdint.h>

void chip_init(void);
uint32_t chip_read(uint32_t offset);
void chip_write(uint32_t offset, uint32_t value);

#endif
