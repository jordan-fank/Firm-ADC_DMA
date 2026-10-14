#ifndef PLATFORM_BYTE_STREAM_H
#define PLATFORM_BYTE_STREAM_H

#include <stdint.h>
#include "platform_status.h"

/**
 * @brief 字节流对象前向声明。
 *
 * service 层通过该句柄发送/接收字节，不关心底层是 USB CDC、串口还是其他链路。
 */
typedef struct platform_byte_stream platform_byte_stream_t;

/**
 * @brief 字节流接收回调类型。
 *
 * @param stream 产生接收事件的字节流对象。
 * @param data 本次收到的数据缓冲区。
 * @param length 本次收到的字节数。
 * @param context 注册回调时传入的用户上下文，一般指向 service 对象。
 */
typedef void (*platform_byte_stream_rx_callback_t)(platform_byte_stream_t *stream,
                                                   const uint8_t *data,
                                                   uint32_t length,
                                                   void *context);

/**
 * @brief 字节流操作表。
 *
 * 抽象“初始化、发送、注册接收回调”三个动作，service 层只调用 ops，
 * 不直接接触 USB CDC vendor 实现。
 *
 * @param init 初始化字节流对象。
 * @param send 发送 data 指向的 length 字节数据。
 * @param set_rx_callback 注册底层收到数据后的上层回调。
 */
typedef struct {
    platform_status_t (*init)(platform_byte_stream_t *stream);
    platform_status_t (*send)(platform_byte_stream_t *stream,
                              const uint8_t *data,
                              uint32_t length);
    platform_status_t (*set_rx_callback)(platform_byte_stream_t *stream,
                                         platform_byte_stream_rx_callback_t callback,
                                         void *context);
} platform_byte_stream_ops_t;

/**
 * @brief 字节流平台对象。
 *
 * @param ops 指向实现层提供的操作表。
 * @param impl 指向实现层私有对象，供 IMPLEMENT 层向下转型。
 */
struct platform_byte_stream {
    const platform_byte_stream_ops_t *ops;
    void *impl;
};

#endif
