/* Chipova Forge: the chip's registers on the MCU's external memory bus (an FSMC NOR/SRAM bank).
 * The chip takes 32-bit accesses only. */
#include "board.h"
#include "forge_chip.h"
#include "stm32f4_min.h"

void chip_init(void)
{
    RCC_AHB3ENR |= 1u;  /* FSMC */
    FSMC_BCR(BOARD_CHIP_FSMC_BANK) = (1u << 12) | (1u << 4) | 1u;
    FSMC_BTR(BOARD_CHIP_FSMC_BANK) = 0x0FFFFF2Fu;
}

uint32_t chip_read(uint32_t offset)
{
    return REG32(BOARD_CHIP_BASE + offset);
}

void chip_write(uint32_t offset, uint32_t value)
{
    REG32(BOARD_CHIP_BASE + offset) = value;
}
