#include "impl_usb_cdc_device.h"
#include "vendor_usb_cdc.h"

/**
 * @brief USB CDC 设备实现对象。
 *
 * @param base 对外暴露的 platform USB 设备基类对象。
 */
typedef struct {
    platform_usb_device_t base;
} impl_usb_cdc_device_t;

static platform_status_t impl_usb_cdc_device_init(platform_usb_device_t *device);

/**
 * @brief USB CDC 设备操作表。
 */
static const platform_usb_device_ops_t impl_usb_cdc_device_ops = {
    impl_usb_cdc_device_init
};

static impl_usb_cdc_device_t impl_usb_cdc_device;

/**
 * @brief 只在第一次使用时绑定 base.ops 和 base.impl。
 */
static void impl_usb_cdc_device_bind(void)
{
    if (impl_usb_cdc_device.base.ops == 0) {
        impl_usb_cdc_device.base.ops = &impl_usb_cdc_device_ops;
        impl_usb_cdc_device.base.impl = &impl_usb_cdc_device;
    }
}

/**
 * @brief 初始化底层 USB CDC 设备协议栈。
 *
 * @param device platform USB 设备对象，本实现不需要使用。
 * @return PLATFORM_OK 表示 vendor USB CDC 初始化成功。
 */
static platform_status_t impl_usb_cdc_device_init(platform_usb_device_t *device)
{
    (void)device;

    if (vendor_usb_cdc_device_init() != VENDOR_USB_CDC_OK) {
        return PLATFORM_ERROR;
    }

    return PLATFORM_OK;
}

/**
 * @brief 获取 USB CDC 设备 platform 对象。
 *
 * @return 指向静态 base 对象的指针。
 */
platform_usb_device_t *impl_usb_cdc_device_get(void)
{
    impl_usb_cdc_device_bind();
    return &impl_usb_cdc_device.base;
}
