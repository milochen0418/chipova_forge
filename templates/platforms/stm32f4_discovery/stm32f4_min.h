/* The few STM32F407 registers Forge's firmware templates use (from RM0090), without CMSIS. */
#ifndef STM32F4_MIN_H
#define STM32F4_MIN_H
#include <stdint.h>

#define REG32(addr) (*(volatile uint32_t *)(addr))

/* Reset and clock control */
#define RCC_BASE        0x40023800u
#define RCC_AHB1ENR     REG32(RCC_BASE + 0x30)
#define RCC_AHB3ENR     REG32(RCC_BASE + 0x38)
#define RCC_APB1ENR     REG32(RCC_BASE + 0x40)
#define RCC_APB2ENR     REG32(RCC_BASE + 0x44)

/* GPIO ports */
#define GPIOA_BASE      0x40020000u
#define GPIOB_BASE      0x40020400u
#define GPIOC_BASE      0x40020800u
#define GPIOD_BASE      0x40020C00u
#define GPIOE_BASE      0x40021000u
#define GPIO_MODER(p)   REG32((p) + 0x00)
#define GPIO_OTYPER(p)  REG32((p) + 0x04)
#define GPIO_PUPDR(p)   REG32((p) + 0x0C)
#define GPIO_IDR(p)     REG32((p) + 0x10)
#define GPIO_ODR(p)     REG32((p) + 0x14)
#define GPIO_BSRR(p)    REG32((p) + 0x18)
#define GPIO_AFRL(p)    REG32((p) + 0x20)
#define GPIO_AFRH(p)    REG32((p) + 0x24)

/* USART */
#define USART1_BASE     0x40011000u
#define USART2_BASE     0x40004400u
#define USART_SR(u)     REG32((u) + 0x00)
#define USART_DR(u)     REG32((u) + 0x04)
#define USART_BRR(u)    REG32((u) + 0x08)
#define USART_CR1(u)    REG32((u) + 0x0C)
#define USART_SR_RXNE   (1u << 5)
#define USART_SR_TXE    (1u << 7)
#define USART_CR1_RE    (1u << 2)
#define USART_CR1_TE    (1u << 3)
#define USART_CR1_RXNEIE (1u << 5)
#define USART_CR1_UE    (1u << 13)

/* I2C (v1) */
#define I2C1_BASE       0x40005400u
#define I2C2_BASE       0x40005800u
#define I2C_CR1(i)      REG32((i) + 0x00)
#define I2C_CR2(i)      REG32((i) + 0x04)
#define I2C_DR(i)       REG32((i) + 0x10)
#define I2C_SR1(i)      REG32((i) + 0x14)
#define I2C_SR2(i)      REG32((i) + 0x18)
#define I2C_CCR(i)      REG32((i) + 0x1C)
#define I2C_TRISE(i)    REG32((i) + 0x20)
#define I2C_CR1_PE      (1u << 0)
#define I2C_CR1_START   (1u << 8)
#define I2C_CR1_STOP    (1u << 9)
#define I2C_CR1_ACK     (1u << 10)
#define I2C_SR1_SB      (1u << 0)
#define I2C_SR1_ADDR    (1u << 1)
#define I2C_SR1_BTF     (1u << 2)
#define I2C_SR1_RXNE    (1u << 6)
#define I2C_SR1_TXE     (1u << 7)
#define I2C_SR1_AF      (1u << 10)

/* SPI */
#define SPI1_BASE       0x40013000u
#define SPI2_BASE       0x40003800u
#define SPI3_BASE       0x40003C00u
#define SPI_CR1(s)      REG32((s) + 0x00)
#define SPI_SR(s)       REG32((s) + 0x08)
#define SPI_DR(s)       REG32((s) + 0x0C)
#define SPI_CR1_MSTR    (1u << 2)
#define SPI_CR1_BR(n)   ((uint32_t)(n) << 3)
#define SPI_CR1_SPE     (1u << 6)
#define SPI_CR1_SSI     (1u << 8)
#define SPI_CR1_SSM     (1u << 9)
#define SPI_SR_RXNE     (1u << 0)
#define SPI_SR_TXE      (1u << 1)
#define SPI_SR_BSY      (1u << 7)

/* External interrupts */
#define SYSCFG_BASE     0x40013800u
#define SYSCFG_EXTICR(n) REG32(SYSCFG_BASE + 0x08 + 4u * (n))
#define EXTI_BASE       0x40013C00u
#define EXTI_IMR        REG32(EXTI_BASE + 0x00)
#define EXTI_RTSR       REG32(EXTI_BASE + 0x08)
#define EXTI_FTSR       REG32(EXTI_BASE + 0x0C)
#define EXTI_PR         REG32(EXTI_BASE + 0x14)

/* FSMC (the external memory bus: NOR/SRAM banks at 0x60000000 + 0x04000000 * n) */
#define FSMC_R_BASE     0xA0000000u
#define FSMC_BCR(n)     REG32(FSMC_R_BASE + 8u * (n))
#define FSMC_BTR(n)     REG32(FSMC_R_BASE + 8u * (n) + 4u)

/* Core: SysTick and the NVIC */
#define SYST_CSR        REG32(0xE000E010u)
#define SYST_RVR        REG32(0xE000E014u)
#define SYST_CVR        REG32(0xE000E018u)
#define NVIC_ISER(n)    REG32(0xE000E100u + 4u * (n))

static inline void nvic_enable(unsigned irq) { NVIC_ISER(irq >> 5) = 1u << (irq & 31u); }
static inline void wait_for_interrupt(void) { __asm volatile("wfi"); }
static inline void irq_disable(void) { __asm volatile("cpsid i" ::: "memory"); }
static inline void irq_enable(void) { __asm volatile("cpsie i" ::: "memory"); }

#endif
