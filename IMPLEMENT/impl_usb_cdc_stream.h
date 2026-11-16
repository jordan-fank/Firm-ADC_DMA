/**
 * @file impl_usb_cdc_stream.h
 * @brief USB CDC 字节流的 platform 实现层接口。
 */

#ifndef IMPL_USB_CDC_STREAM_H
#define IMPL_USB_CDC_STREAM_H

#include "platform_byte_stream.h"

/**
 * @brief 获取 USB CDC 字节流 platform 对象。
 *
 * @return 指向静态 platform_byte_stream_t 对象的指针。
 */
platform_byte_stream_t *impl_usb_cdc_stream_get(void);

#endif
