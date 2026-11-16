/**
 * @file platform_debug_uart.h
 * @brief 调试串口抽象接口（纯头文件，不含任何 STM32 实现）。
 *
 * 当前只抽象“初始化调试串口”一个动作；输出本身由标准库 printf 触发，
 * 字符如何送到串口由 IMPLEMENT 层负责，APP 不接触任何寄存器。
 */

#ifndef PLATFORM_DEBUG_UART_H
#define PLATFORM_DEBUG_UART_H

#include "platform_status.h"

/**
 * @brief 调试串口对象前向声明。
 *
 * platform 层只公开对象句柄，具体成员由 struct platform_debug_uart 在本文件末尾定义，
 * 具体实现由 implement 层通过 impl 指针绑定。
 */
typedef struct platform_debug_uart platform_debug_uart_t;

/**
 * @brief 调试串口操作表。
 *
 * @param init 初始化底层调试串口。
 */
typedef struct {
    platform_status_t (*init)(platform_debug_uart_t *uart);
} platform_debug_uart_ops_t;

/**
 * @brief 调试串口平台对象。
 *
 * @param ops 指向实现层提供的操作表，用于多态分发。
 * @param impl 指向实现层私有对象，platform/app 不解析它的具体类型。
 */
struct platform_debug_uart {
    const platform_debug_uart_ops_t *ops;
    void *impl;
};

#endif
