/**
 * @file platform_board.h
 * @brief 板级对象集合：把当前板卡用到的 platform 对象打包给 service 层。
 *
 * service/app 通过 platform_board_get() 一次性取得 ADC 流、字节流、USB 设备和
 * 调试串口，不关心这些对象在 IMPLEMENT 层是如何组装出来的。
 */

#ifndef PLATFORM_BOARD_H
#define PLATFORM_BOARD_H

#include "platform_adc_stream.h"
#include "platform_byte_stream.h"
#include "platform_debug_uart.h"
#include "platform_usb_device.h"

/**
 * @brief 板级对象集合。
 *
 * 把当前板子上使用的 ADC 采样流、字节流、USB 设备和调试串口集中暴露给上层。
 * 上层只依赖这些 platform 抽象指针，不直接包含 IMPLEMENT/VENDOR 头文件。
 *
 * @param adc_stream 编码器触发 ADC 采样流对象。
 * @param byte_stream USB CDC 字节发送/接收流对象。
 * @param usb_device USB CDC 设备初始化对象。
 * @param debug_uart 调试串口对象（USART1）。
 */
typedef struct {
    platform_adc_stream_t *adc_stream;
    platform_byte_stream_t *byte_stream;
    platform_usb_device_t *usb_device;
    platform_debug_uart_t *debug_uart;
} platform_board_t;

/**
 * @brief 获取当前工程绑定的板级对象集合。
 *
 * @return 指向静态 platform_board_t 的只读指针，失败时理论上返回 0。
 */
const platform_board_t *platform_board_get(void);

/**
 * [NEW] 控制板级使能引脚（当前 STM32 板为 PB11）。
 *
 * @param high 非 0：输出高电平（使能）；0：输出低电平（失能）。
 * @return PLATFORM_OK 表示命令已下发。
 *
 * 说明：这是板级电源/使能控制，不属于 ADC 采样流本身，所以放在 board 级接口，
 * SERVICE 层只调用本函数，不直接操作任何 GPIO。
 */
platform_status_t platform_board_set_enable(uint8_t high);

#endif
