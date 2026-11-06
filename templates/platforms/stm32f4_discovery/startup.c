/* Start-up code for the STM32F407: the vector table, and the reset handler that copies .data,
 * clears .bss and calls main(). Interrupt handlers are weak: firmware defines the ones it uses
 * (SysTick_Handler, USART2_IRQHandler, EXTI1_IRQHandler, ...). */
#include <stdint.h>

extern uint32_t _estack, _etext, _sdata, _edata, _sbss, _ebss;
int main(void);

void Reset_Handler(void)
{
    uint32_t *src = &_etext, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;
    for (dst = &_sbss; dst < &_ebss;) *dst++ = 0;
    main();
    for (;;) {}
}

void Default_Handler(void) { for (;;) {} }

#define WEAK __attribute__((weak, alias("Default_Handler")))
void NMI_Handler(void) WEAK;
void HardFault_Handler(void) WEAK;
void SVC_Handler(void) WEAK;
void PendSV_Handler(void) WEAK;
void SysTick_Handler(void) WEAK;
void EXTI0_IRQHandler(void) WEAK;
void EXTI1_IRQHandler(void) WEAK;
void EXTI2_IRQHandler(void) WEAK;
void EXTI3_IRQHandler(void) WEAK;
void EXTI4_IRQHandler(void) WEAK;
void I2C1_EV_IRQHandler(void) WEAK;
void I2C1_ER_IRQHandler(void) WEAK;
void SPI1_IRQHandler(void) WEAK;
void USART1_IRQHandler(void) WEAK;
void USART2_IRQHandler(void) WEAK;

/* 16 system exceptions, then the 82 interrupts of the STM32F407. */
__attribute__((section(".isr_vector"), used))
void (*const vector_table[16 + 82])(void) = {
    (void (*)(void))(&_estack),
    Reset_Handler,
    NMI_Handler,
    HardFault_Handler,
    [11] = SVC_Handler,
    [14] = PendSV_Handler,
    [15] = SysTick_Handler,
    [16 + 6] = EXTI0_IRQHandler,
    [16 + 7] = EXTI1_IRQHandler,
    [16 + 8] = EXTI2_IRQHandler,
    [16 + 9] = EXTI3_IRQHandler,
    [16 + 10] = EXTI4_IRQHandler,
    [16 + 31] = I2C1_EV_IRQHandler,
    [16 + 32] = I2C1_ER_IRQHandler,
    [16 + 35] = SPI1_IRQHandler,
    [16 + 37] = USART1_IRQHandler,
    [16 + 38] = USART2_IRQHandler,
};
