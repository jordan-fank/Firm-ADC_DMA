#ifndef VENDOR_USB_CDC_H
#define VENDOR_USB_CDC_H

#include <stdint.h>

/**
 * @brief USB CDC vendor 层状态码。
 */
typedef enum {
    VENDOR_USB_CDC_OK = 0,
    VENDOR_USB_CDC_BUSY,
    VENDOR_USB_CDC_INVALID_PARAM,
    VENDOR_USB_CDC_ERROR
} vendor_usb_cdc_status_t;

/**
 * @brief USB CDC 接收回调类型。
 *
 * @param data USB OUT 端点收到的数据缓冲区。
 * @param length 本次收到的字节数。
 * @param context 注册回调时传入的用户上下文。
 */
typedef void (*vendor_usb_cdc_rx_handler_t)(const uint8_t *data,
                                            uint32_t length,
                                            void *context);

/**
 * @brief 初始化 USB CDC 设备协议栈。
 *
 * @return VENDOR_USB_CDC_OK 表示 USBD_Init 已调用完成。
 */
vendor_usb_cdc_status_t vendor_usb_cdc_device_init(void);

/**
 * @brief 写入 USB CDC 发送环形缓冲区。
 *
 * @param data 待发送数据缓冲区。
 * @param length 待发送字节数。
 * @return VENDOR_USB_CDC_OK 表示写入成功，VENDOR_USB_CDC_BUSY 表示发送缓冲区空间不足。
 */
vendor_usb_cdc_status_t vendor_usb_cdc_write(const uint8_t *data, uint32_t length);

/**
 * @brief 注册 USB CDC 接收回调。
 *
 * @param handler 收到 USB OUT 数据时调用的回调函数。
 * @param context 传给 handler 的用户上下文。
 */
void vendor_usb_cdc_set_rx_handler(vendor_usb_cdc_rx_handler_t handler, void *context);

/**
 * @brief 从 USB CDC 类驱动转入本模块的接收处理入口。
 *
 * @param data USB OUT 端点收到的数据缓冲区。
 * @param length 本次收到的字节数。
 */
void vendor_usb_cdc_handle_rx_packet(const uint8_t *data, uint32_t length);

/**
 * @brief 查询 USB CDC 发送环形缓冲区剩余空间。
 *
 * @return 还能写入的字节数。
 */
uint32_t vendor_usb_cdc_get_tx_free(void);

#endif
