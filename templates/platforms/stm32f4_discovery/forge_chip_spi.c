/* Chipova Forge: the chip's registers over SPI, through the chip's SPI-to-APB bridge.
 *
 * A frame, with the chip selected (its CS pin low): a command byte (bit 7 = 1 to write, bits
 * 6..0 = the register's index, offset / 4), then four data bytes, most significant first. For a
 * read, the chip answers in those four bytes. SPI mode 0, at most clk / 8 of the chip. */
#include "board.h"
#include "forge_chip.h"
#include "stm32f4_min.h"

static void gpio_mode(uint32_t port, unsigned pin, uint32_t mode)
{
    GPIO_MODER(port) = (GPIO_MODER(port) & ~(3u << (2 * pin))) | (mode << (2 * pin));
}

static void gpio_af(uint32_t port, unsigned pin, uint32_t af)
{
    gpio_mode(port, pin, 2u);
    if (pin < 8) GPIO_AFRL(port) = (GPIO_AFRL(port) & ~(0xFu << (4 * pin))) | (af << (4 * pin));
    else GPIO_AFRH(port) = (GPIO_AFRH(port) & ~(0xFu << (4 * (pin - 8)))) | (af << (4 * (pin - 8)));
}

static void select(int on)
{
    GPIO_BSRR(BOARD_CHIP_CS_PORT) = on ? (1u << (BOARD_CHIP_CS_PIN + 16)) : (1u << BOARD_CHIP_CS_PIN);
}

static uint8_t xfer(uint8_t b)
{
    while (!(SPI_SR(BOARD_CHIP_SPI) & SPI_SR_TXE)) {}
    SPI_DR(BOARD_CHIP_SPI) = b;
    while (!(SPI_SR(BOARD_CHIP_SPI) & SPI_SR_RXNE)) {}
    return (uint8_t)SPI_DR(BOARD_CHIP_SPI);
}

void chip_init(void)
{
    BOARD_CHIP_SPI_CLOCK_ON();
    gpio_af(BOARD_CHIP_SCK_PORT, BOARD_CHIP_SCK_PIN, BOARD_CHIP_SPI_AF);
    gpio_af(BOARD_CHIP_MISO_PORT, BOARD_CHIP_MISO_PIN, BOARD_CHIP_SPI_AF);
    gpio_af(BOARD_CHIP_MOSI_PORT, BOARD_CHIP_MOSI_PIN, BOARD_CHIP_SPI_AF);
    select(0);
    gpio_mode(BOARD_CHIP_CS_PORT, BOARD_CHIP_CS_PIN, 1u);
    /* Master, mode 0, 8 bits, MSB first, the chip select by software: fPCLK / 16 (1 MHz). */
    SPI_CR1(BOARD_CHIP_SPI) = SPI_CR1_MSTR | SPI_CR1_BR(3) | SPI_CR1_SSM | SPI_CR1_SSI;
    SPI_CR1(BOARD_CHIP_SPI) |= SPI_CR1_SPE;
}

uint32_t chip_read(uint32_t offset)
{
    select(1);
    xfer((uint8_t)((offset >> 2) & 0x7Fu));
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v = (v << 8) | xfer(0);
    while (SPI_SR(BOARD_CHIP_SPI) & SPI_SR_BSY) {}
    select(0);
    return v;
}

void chip_write(uint32_t offset, uint32_t value)
{
    select(1);
    xfer((uint8_t)(0x80u | ((offset >> 2) & 0x7Fu)));
    for (int i = 3; i >= 0; i--) xfer((uint8_t)(value >> (8 * i)));
    while (SPI_SR(BOARD_CHIP_SPI) & SPI_SR_BSY) {}
    select(0);
}
