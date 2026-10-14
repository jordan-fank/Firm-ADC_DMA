#ifndef IMPL_USB_CDC_DEVICE_H
#define IMPL_USB_CDC_DEVICE_H

#include "platform_usb_device.h"

/**
 * @brief 获取 USB CDC 设备 platform 对象。
 *
 * @return 指向静态 platform_usb_device_t 对象的指针。
 */
platform_usb_device_t *impl_usb_cdc_device_get(void);

#endif
