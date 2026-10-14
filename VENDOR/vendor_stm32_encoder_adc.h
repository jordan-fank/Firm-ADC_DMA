#ifndef VENDOR_STM32_ENCODER_ADC_H
#define VENDOR_STM32_ENCODER_ADC_H

#include <stdint.h>

/**
 * @brief STM32 编码器触发 ADC vendor 层状态码。
 */
typedef enum {
    VENDOR_STM32_ENCODER_ADC_OK = 0,
    VENDOR_STM32_ENCODER_ADC_ERROR,
    VENDOR_STM32_ENCODER_ADC_INVALID_PARAM
} vendor_stm32_encoder_adc_status_t;

/**
 * @brief ADC 采样块回调类型。
 *
 * @param samples DMA 半缓冲中取出的 8bit ADC 原始采样数组。
 * @param sample_count 本次数组中的有效采样数。
 * @param context 注册回调时传入的用户上下文。
 */
typedef void (*vendor_stm32_encoder_adc_sample_handler_t)(const uint8_t *samples,
                                                          uint32_t sample_count,
                                                          void *context);

/**
 * @brief STM32 编码器触发 ADC 初始化配置。
 *
 * @param trigger_counts_per_sample 保留的接口参数；新板固定为PA5每个上升沿触发一次ADC。
 * @param speed_update_ms Z相转速结果的上报周期，同时作为停转超时。
 * @param encoder_counts_per_rev 保留的编码器参数；Z相周期测速不再使用它换算RPM。
 * @param irq_preempt_priority DMA interrupt preemption priority; Z EXTI uses the next level.
 * @param irq_sub_priority DMA interrupt subpriority; Z EXTI uses the next level.
 */
typedef struct {
    uint16_t trigger_counts_per_sample;
    uint16_t speed_update_ms;
    uint16_t encoder_counts_per_rev;
    uint8_t irq_preempt_priority;
    uint8_t irq_sub_priority;
} vendor_stm32_encoder_adc_config_t;

/**
 * @brief vendor 层转速读取结果。
 *
 * @param rpm 最近一次统计窗口换算得到的转速。
 * @param updated 非 0 表示有新窗口结果，读取后会被清零。
 */
typedef struct {
    uint32_t rpm;
    uint8_t updated;
} vendor_stm32_encoder_adc_speed_t;

/**
 * @brief UART debug snapshot of the encoder/ADC/DMA acquisition pipeline.
 *
 * All counters are reset when a new acquisition starts.  The snapshot is
 * copied atomically, so the foreground UART logger never reads half-updated
 * interrupt statistics.
 */
typedef struct {
    uint32_t timestamp_us;
    uint32_t rpm;
    uint32_t dma_half_irq_count;
    uint32_t dma_full_irq_count;
    uint32_t samples_emitted;
    uint32_t z_irq_count;
    uint32_t last_z_period_us;
    uint32_t dma_transfer_error_count;
    uint32_t dma_direct_error_count;
    uint32_t dma_fifo_error_count;
    uint32_t tim2_capture1;
    uint16_t dma_remaining;
    uint8_t adc_min;
    uint8_t adc_max;
    uint8_t adc_last;
    uint8_t adc_sample_seen;
    uint8_t adc_overrun;
    uint8_t acquisition_active;
    uint8_t adc_enabled;
    uint8_t dma_enabled;
    uint8_t tim2_enabled;
    uint8_t phase_b_level;
    uint8_t tim2_capture_seen;
    uint8_t tim2_capture_overflow;
} vendor_stm32_encoder_adc_debug_t;

/**
 * @brief 初始化PA5单边沿采样、PB12 Z相测速以及ADC/DMA。
 *
 * @param config 硬件采样和中断配置。
 * @return VENDOR_STM32_ENCODER_ADC_OK 表示初始化完成。
 */
vendor_stm32_encoder_adc_status_t vendor_stm32_encoder_adc_init(const vendor_stm32_encoder_adc_config_t *config);

/**
 * @brief 启动编码器触发 ADC 采样和转速统计。
 */
void vendor_stm32_encoder_adc_start(void);

/**
 * @brief 停止采样和转速统计，并尽量吐出 DMA 中剩余采样。
 */
void vendor_stm32_encoder_adc_stop(void);

/**
 * @brief 暂停新的 ADC 采样，同时保持 TIM2/TIM5 转速计数连续。
 */
void vendor_stm32_encoder_adc_pause(void);

/**
 * @brief 恢复 ADC 采样。
 */
void vendor_stm32_encoder_adc_resume(void);

/**
 * @brief 注册 ADC 采样块回调。
 *
 * @param handler DMA 半满/满或 stop 刷新剩余数据时调用的回调。
 * @param context 回调上下文，会原样传回 handler。
 */
void vendor_stm32_encoder_adc_set_sample_handler(vendor_stm32_encoder_adc_sample_handler_t handler,
                                                 void *context);

/**
 * @brief 读取最近一次转速更新。
 *
 * @param speed 输出参数，保存 rpm 和 updated 标志。
 * @return VENDOR_STM32_ENCODER_ADC_OK 表示读取成功。
 */
vendor_stm32_encoder_adc_status_t vendor_stm32_encoder_adc_get_speed_update(vendor_stm32_encoder_adc_speed_t *speed);

/** @brief Copy the current acquisition diagnostics for foreground logging. */
void vendor_stm32_encoder_adc_get_debug(vendor_stm32_encoder_adc_debug_t *debug);

/**
 * @brief ADC1 中断处理入口，清除 EOC/OVR 标志。
 */
void vendor_stm32_encoder_adc_adc_irq_handler(void);

/**
 * @brief DMA2 Stream0 中断处理入口，处理半满/满采样数据。
 */
void vendor_stm32_encoder_adc_dma_irq_handler(void);

/**
 * @brief Legacy TIM2 update IRQ entry; single-edge sampling does not enable it.
 */
void vendor_stm32_encoder_adc_tim_irq_handler(void);

/**
 * @brief 保留的TIM3兼容入口，新版不再用TIM3统计转速。
 */
void vendor_stm32_encoder_adc_speed_irq_handler(void);

/** @brief PB12/EXTI12 Z相中断入口，通过TIM5时间差计算RPM。 */
void vendor_stm32_encoder_adc_z_irq_handler(void);

#endif
