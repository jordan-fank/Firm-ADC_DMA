/**
 * @file impl_usb_cdc_stream.c
 * @brief 实现层：把 platform_byte_stream 抽象适配到 USB CDC 驱动。
 *
 * 本文件只做状态码映射和回调桥接，真正的 USB 收发在 VENDOR/vendor_usb_cdc.c。
 * 数据通路：SERVICE 调用 send() -> vendor_usb_cdc_write() -> ST CDC 发送环。
 */

#include "impl_usb_cdc_stream.h"
#include "vendor_usb_cdc.h"

/**
 * @brief USB CDC 字节流实现对象。
 *
 * @param base 对外暴露的 platform 字节流基类对象。
 * @param rx_callback service 注册的接收回调。
 * @param rx_context service 注册回调时传入的上下文。
 */
typedef struct {
    platform_byte_stream_t base;
    platform_byte_stream_rx_callback_t rx_callback;
    void *rx_context;
} impl_usb_cdc_stream_t;

static platform_status_t impl_usb_cdc_stream_init(platform_byte_stream_t *stream);
static platform_status_t impl_usb_cdc_stream_send(platform_byte_stream_t *stream,
                                                  const uint8_t *data,
                                                  uint32_t length);
static platform_status_t impl_usb_cdc_stream_set_rx_callback(platform_byte_stream_t *stream,
                                                             platform_byte_stream_rx_callback_t callback,
                                                             void *context);

/**
 * @brief USB CDC 字节流操作表。
 *
 * service 通过 platform_byte_stream_t.ops 调用这里的 init/send/set_rx_callback。
 */
static const platform_byte_stream_ops_t impl_usb_cdc_stream_ops = {
    impl_usb_cdc_stream_init,
    impl_usb_cdc_stream_send,
    impl_usb_cdc_stream_set_rx_callback
};

static impl_usb_cdc_stream_t impl_usb_cdc_stream;

/**
 * @brief 只在第一次使用时绑定 base.ops 和 base.impl。
 */
static void impl_usb_cdc_stream_bind(void)
{
    if (impl_usb_cdc_stream.base.ops == 0) {
        impl_usb_cdc_stream.base.ops = &impl_usb_cdc_stream_ops;
        impl_usb_cdc_stream.base.impl = &impl_usb_cdc_stream;
    }
}

/**
 * @brief 从 platform 字节流对象取回实现层私有对象。
 *
 * @param stream platform 字节流对象。
 * @return 成功时返回 impl_usb_cdc_stream_t 指针，参数为空时返回 0。
 */
static impl_usb_cdc_stream_t *impl_usb_cdc_stream_from_base(platform_byte_stream_t *stream)
{
    if (stream == 0) {
        return 0;
    }

    return (impl_usb_cdc_stream_t *)stream->impl;
}

/**
 * @brief 把 vendor_usb_cdc_status_t 映射成 platform_status_t。
 *
 * @param status vendor 层 USB CDC 返回状态。
 * @return platform 层统一状态码。
 */
static platform_status_t impl_usb_cdc_stream_map_status(vendor_usb_cdc_status_t status)
{
    switch (status) {
    case VENDOR_USB_CDC_OK:
        return PLATFORM_OK;
    case VENDOR_USB_CDC_BUSY:
        return PLATFORM_BUSY;
    case VENDOR_USB_CDC_INVALID_PARAM:
        return PLATFORM_INVALID_PARAM;
    default:
        return PLATFORM_ERROR;
    }
}

/**
 * @brief vendor USB RX 回调到 service RX 回调的桥接函数。
 *
 * @param data vendor 层收到的数据缓冲区。
 * @param length 本次收到的字节数。
 * @param context 注册到 vendor 层的 impl_usb_cdc_stream_t 对象指针。
 */
static void impl_usb_cdc_stream_vendor_rx(const uint8_t *data, uint32_t length, void *context)
{
    impl_usb_cdc_stream_t *self = (impl_usb_cdc_stream_t *)context;

    if ((self != 0) && (self->rx_callback != 0)) {
        self->rx_callback(&self->base, data, length, self->rx_context);
    }
}

/**
 * @brief 初始化 USB CDC 字节流。
 *
 * @param stream platform 字节流对象。
 * @return PLATFORM_OK 表示 RX 桥接回调注册完成。
 */
static platform_status_t impl_usb_cdc_stream_init(platform_byte_stream_t *stream)
{
    impl_usb_cdc_stream_t *self = impl_usb_cdc_stream_from_base(stream);

    if (self == 0) {
        return PLATFORM_INVALID_PARAM;
    }

    vendor_usb_cdc_set_rx_handler(impl_usb_cdc_stream_vendor_rx, self);
    return PLATFORM_OK;
}

/**
 * @brief 通过 vendor USB CDC 写入一段字节。
 *
 * @param stream platform 字节流对象，本实现不需要使用。
 * @param data 待发送数据缓冲区。
 * @param length 待发送字节数。
 * @return platform 层统一状态码。
 */
static platform_status_t impl_usb_cdc_stream_send(platform_byte_stream_t *stream,
                                                  const uint8_t *data,
                                                  uint32_t length)
{
    (void)stream;
    return impl_usb_cdc_stream_map_status(vendor_usb_cdc_write(data, length));
}

/**
 * @brief 保存 service 注册的 USB RX 回调。
 *
 * @param stream platform 字节流对象。
 * @param callback USB 收到数据后要调用的 service 回调。
 * @param context 回调上下文，通常是 adc_stream_service_t。
 * @return PLATFORM_OK 表示保存成功。
 */
static platform_status_t impl_usb_cdc_stream_set_rx_callback(platform_byte_stream_t *stream,
                                                             platform_byte_stream_rx_callback_t callback,
                                                             void *context)
{
    impl_usb_cdc_stream_t *self = impl_usb_cdc_stream_from_base(stream);

    if (self == 0) {
        return PLATFORM_INVALID_PARAM;
    }

    self->rx_callback = callback;
    self->rx_context = context;
    return PLATFORM_OK;
}

/**
 * @brief 获取 USB CDC 字节流 platform 对象。
 *
 * @return 指向静态 base 对象的指针。
 */
platform_byte_stream_t *impl_usb_cdc_stream_get(void)
{
    impl_usb_cdc_stream_bind();
    return &impl_usb_cdc_stream.base;
}
