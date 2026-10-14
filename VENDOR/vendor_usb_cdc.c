#include "vendor_usb_cdc.h"
#include "usb_conf.h"
#include "usbd_conf.h"
#include "usbd_core.h"
#include "usbd_cdc_core.h"
#include "usbd_desc.h"
#include "usbd_usr.h"
#include <string.h>

extern uint8_t APP_Rx_Buffer[];
extern __IO uint32_t APP_Rx_ptr_in;
extern __IO uint32_t APP_Rx_ptr_out;

#ifdef USB_OTG_HS_INTERNAL_DMA_ENABLED
#if defined(__ICCARM__)
#pragma data_alignment = 4
#endif
#endif
__ALIGN_BEGIN USB_OTG_CORE_HANDLE USB_OTG_dev __ALIGN_END;

static vendor_usb_cdc_rx_handler_t vendor_usb_cdc_rx_handler = 0;
static void *vendor_usb_cdc_rx_context = 0;

/**
 * @brief 查询 USB CDC 发送环形缓冲区剩余空间。
 *
 * APP_Rx_Buffer/APP_Rx_ptr_in/APP_Rx_ptr_out 是 ST CDC 类驱动使用的发送环形缓冲区。
 *
 * @return 当前可继续写入的字节数。
 */
uint32_t vendor_usb_cdc_get_tx_free(void)
{
    uint32_t in = APP_Rx_ptr_in;
    uint32_t out = APP_Rx_ptr_out;

    if (in >= out) {
        return (APP_RX_DATA_SIZE - (in - out) - 1U);
    }

    return (out - in - 1U);
}

/**
 * @brief 初始化 USB CDC 设备协议栈。
 *
 * 内部调用 ST USB Device Library 的 USBD_Init，并按 USE_USB_OTG_HS 选择 HS/FS core。
 *
 * @return VENDOR_USB_CDC_OK 表示初始化流程已发起。
 */
vendor_usb_cdc_status_t vendor_usb_cdc_device_init(void)
{
    USBD_Init(&USB_OTG_dev,
#ifdef USE_USB_OTG_HS
              USB_OTG_HS_CORE_ID,
#else
              USB_OTG_FS_CORE_ID,
#endif
              &USR_desc,
              &USBD_CDC_cb,
              &USR_cb);

    return VENDOR_USB_CDC_OK;
}

/**
 * @brief 把数据写入 ST CDC 类驱动的发送环形缓冲区。
 *
 * @param data 待发送数据缓冲区；length 为 0 时允许 data 为 0。
 * @param length 待写入字节数。
 * @return VENDOR_USB_CDC_OK 表示写入成功，BUSY 表示空间不足。
 */
vendor_usb_cdc_status_t vendor_usb_cdc_write(const uint8_t *data, uint32_t length)
{
    uint32_t write_index;
    uint32_t first_count;
    uint32_t new_write_index;

    if ((data == 0) && (length > 0U)) {
        return VENDOR_USB_CDC_INVALID_PARAM;
    }

    if (length > vendor_usb_cdc_get_tx_free()) {
        return VENDOR_USB_CDC_BUSY;
    }

    if (length == 0U) {
        return VENDOR_USB_CDC_OK;
    }

    write_index = APP_Rx_ptr_in;
    first_count = APP_RX_DATA_SIZE - write_index;
    if (first_count > length) {
        first_count = length;
    }

    memcpy(&APP_Rx_Buffer[write_index], data, first_count);
    if (length > first_count) {
        memcpy(&APP_Rx_Buffer[0], &data[first_count], length - first_count);
    }

    new_write_index = write_index + length;
    if (new_write_index >= APP_RX_DATA_SIZE) {
        new_write_index -= APP_RX_DATA_SIZE;
    }

    /* Publish only fully copied data to the USB interrupt consumer. */
    APP_Rx_ptr_in = new_write_index;

    return VENDOR_USB_CDC_OK;
}

/**
 * @brief 注册 USB CDC 接收回调。
 *
 * @param handler 收到 USB OUT 数据时调用的回调。
 * @param context 回调上下文，会原样传回 handler。
 */
void vendor_usb_cdc_set_rx_handler(vendor_usb_cdc_rx_handler_t handler, void *context)
{
    vendor_usb_cdc_rx_handler = handler;
    vendor_usb_cdc_rx_context = context;
}

/**
 * @brief 处理 USB CDC 类驱动上送的 OUT 数据包。
 *
 * @param data USB OUT 数据缓冲区。
 * @param length USB OUT 数据长度。
 */
void vendor_usb_cdc_handle_rx_packet(const uint8_t *data, uint32_t length)
{
    if (vendor_usb_cdc_rx_handler != 0) {
        vendor_usb_cdc_rx_handler(data, length, vendor_usb_cdc_rx_context);
    }
}
