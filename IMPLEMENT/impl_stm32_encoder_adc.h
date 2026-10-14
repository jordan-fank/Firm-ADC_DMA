#ifndef IMPL_STM32_ENCODER_ADC_H
#define IMPL_STM32_ENCODER_ADC_H

#include "platform_adc_stream.h"

/**
 * @brief 获取 STM32 编码器触发 ADC 的 platform 采样流对象。
 *
 * @return 指向静态 platform_adc_stream_t 对象的指针。
 */
platform_adc_stream_t *impl_stm32_encoder_adc_get_stream(void);

/**
 * @brief DMA2 Stream0 中断转接入口。
 *
 * 当前头文件只暴露 DMA IRQ 入口；其他中断入口在 c 文件中由启动文件直接引用。
 */
void impl_stm32_encoder_adc_dma_irq_handler(void);

#endif
