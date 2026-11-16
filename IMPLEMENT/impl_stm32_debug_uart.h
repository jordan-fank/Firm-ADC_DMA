/**
 * @file impl_stm32_debug_uart.h
 * @brief 调试串口的 platform 实现层接口。
 */

#ifndef IMPL_STM32_DEBUG_UART_H
#define IMPL_STM32_DEBUG_UART_H

#include "platform_debug_uart.h"

/**
 * @brief 获取 STM32 调试串口的 platform 对象。
 *
 * @return 指向静态 platform_debug_uart_t 对象的指针。
 */
platform_debug_uart_t *impl_stm32_debug_uart_get(void);

#endif
