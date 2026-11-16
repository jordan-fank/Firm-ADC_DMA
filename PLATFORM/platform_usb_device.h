/**
 * @file platform_usb_device.h
 * @brief USB 设备抽象接口（纯头文件，不含任何 STM32 实现）。
 *
 * 当前只抽象“初始化 USB 协议栈”一个动作，后续如需反初始化、
 * 挂起/恢复，可在 ops 表中继续扩展。
 */

#ifndef PLATFORM_USB_DEVICE_H
#define PLATFORM_USB_DEVICE_H

#include "platform_status.h"

/**
 * @brief USB 设备对象前向声明。
 *
 * platform 层只公开对象句柄，具体成员由 struct platform_usb_device 在本文件末尾定义，
 * 具体实现由 implement 层通过 impl 指针绑定。
 */
typedef struct platform_usb_device platform_usb_device_t;

/**
 * @brief USB 设备操作表。
 *
 * 当前只抽象设备初始化动作，后续如果需要反初始化、挂起、恢复，
 * 可以在此 ops 表继续扩展。
 *
 * @param init 初始化底层 USB 设备栈。
 */
typedef struct {
    platform_status_t (*init)(platform_usb_device_t *device);
} platform_usb_device_ops_t;

/**
 * @brief USB 设备平台对象。
 *
 * @param ops 指向实现层提供的操作表，用于多态分发。
 * @param impl 指向实现层私有对象，platform/service 不解析它的具体类型。
 */
struct platform_usb_device {
    const platform_usb_device_ops_t *ops;
    void *impl;
};

#endif
