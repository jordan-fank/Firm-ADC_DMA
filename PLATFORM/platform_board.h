#ifndef PLATFORM_BOARD_H
#define PLATFORM_BOARD_H

#include "platform_adc_stream.h"
#include "platform_byte_stream.h"
#include "platform_usb_device.h"

/**
 * @brief 板级对象集合。
 *
 * 把当前板子上使用的 ADC 采样流、字节流和 USB 设备集中暴露给 service 层。
 * service 只依赖这些 platform 抽象指针，不直接包含 IMPLEMENT/VENDOR 头文件。
 *
 * @param adc_stream 编码器触发 ADC 采样流对象。
 * @param byte_stream USB CDC 字节发送/接收流对象。
 * @param usb_device USB CDC 设备初始化对象。
 */
typedef struct {
    platform_adc_stream_t *adc_stream;
    platform_byte_stream_t *byte_stream;
    platform_usb_device_t *usb_device;
} platform_board_t;

/**
 * @brief 获取当前工程绑定的板级对象集合。
 *
 * @return 指向静态 platform_board_t 的只读指针，失败时理论上返回 0。
 */
const platform_board_t *platform_board_get(void);

#endif
