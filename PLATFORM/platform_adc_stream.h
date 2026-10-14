#ifndef PLATFORM_ADC_STREAM_H
#define PLATFORM_ADC_STREAM_H

#include <stdint.h>
#include "platform_status.h"

/**
 * @brief ADC 采样流对象前向声明。
 *
 * service 层只持有该抽象句柄，具体是 STM32 TIM2 编码器触发 ADC1 DMA
 * 还是其他硬件方案，由 implement 层决定。
 */
typedef struct platform_adc_stream platform_adc_stream_t;

/**
 * @brief ADC 采样流生命周期状态。
 */
typedef enum {
    PLATFORM_ADC_STREAM_UNINIT = 0,
    PLATFORM_ADC_STREAM_READY,
    PLATFORM_ADC_STREAM_RUNNING,
    PLATFORM_ADC_STREAM_PAUSED,
    PLATFORM_ADC_STREAM_ERROR
} platform_adc_stream_state_t;

/**
 * @brief ADC 采样流初始化配置。
 *
 * @param trigger_counts_per_sample 编码器累计多少个计数触发一次 ADC 采样。
 * @param speed_update_ms Speed-result reporting interval and stopped-rotor timeout.
 * @param encoder_counts_per_rev Retained for platform compatibility; Z-period RPM does not use it.
 */
typedef struct {
    uint16_t trigger_counts_per_sample;
    uint16_t speed_update_ms;
    uint16_t encoder_counts_per_rev;
} platform_adc_stream_config_t;

/**
 * @brief 转速更新结果。
 *
 * @param rpm 最近一次计算得到的转速，单位为 rpm。
 * @param updated 非 0 表示本次读取到了新的转速窗口结果，读取后底层会清除更新标志。
 */
typedef struct {
    uint32_t rpm;
    uint8_t updated;
} platform_adc_stream_speed_t;

/**
 * @brief ADC 采样块回调类型。
 *
 * @param stream 产生采样的 ADC 采样流对象。
 * @param samples 本次到达的 8bit ADC 原始采样数组。
 * @param sample_count 本次数组中的有效采样数。
 * @param context 注册回调时传入的用户上下文，一般指向 service 对象。
 */
typedef void (*platform_adc_stream_sample_callback_t)(platform_adc_stream_t *stream,
                                                       const uint8_t *samples,
                                                       uint32_t sample_count,
                                                       void *context);

/**
 * @brief ADC 采样流操作表。
 *
 * 这是 platform 层最核心的抽象接口。service 层通过这些函数控制采样，
 * implement 层把这些函数映射到具体 STM32 vendor 驱动。
 *
 * @param init 按 config 配置采样硬件。
 * @param start 启动采样和转速统计。
 * @param stop 停止采样，并让底层尽量吐出 DMA 中剩余数据。
 * @param pause 暂停新的采样触发，常用于 USB 发送压力大时保护队列。
 * @param resume 恢复 pause 后的采样触发。
 * @param set_sample_callback 注册一批采样到达时的回调。
 * @param get_speed_update 读取最近一次转速更新。
 * @param get_state 获取采样流当前状态。
 */
typedef struct {
    platform_status_t (*init)(platform_adc_stream_t *stream,
                              const platform_adc_stream_config_t *config);
    platform_status_t (*start)(platform_adc_stream_t *stream);
    platform_status_t (*stop)(platform_adc_stream_t *stream);
    platform_status_t (*pause)(platform_adc_stream_t *stream);
    platform_status_t (*resume)(platform_adc_stream_t *stream);
    platform_status_t (*set_sample_callback)(platform_adc_stream_t *stream,
                                             platform_adc_stream_sample_callback_t callback,
                                             void *context);
    platform_status_t (*get_speed_update)(platform_adc_stream_t *stream,
                                          platform_adc_stream_speed_t *speed);
    platform_adc_stream_state_t (*get_state)(platform_adc_stream_t *stream);
} platform_adc_stream_ops_t;

/**
 * @brief ADC 采样流平台对象。
 *
 * @param ops 指向实现层提供的操作表。
 * @param impl 指向实现层私有对象，供 IMPLEMENT 层向下转型。
 */
struct platform_adc_stream {
    const platform_adc_stream_ops_t *ops;
    void *impl;
};

#endif
