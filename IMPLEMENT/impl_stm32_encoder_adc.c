/**
 * @file impl_stm32_encoder_adc.c
 * @brief 实现层：把 platform_adc_stream 抽象适配到 STM32 编码器触发 ADC 驱动。
 *
 * 分层位置：SERVICE -> PLATFORM(抽象) -> IMPLEMENT(本文件) -> VENDOR(寄存器驱动)。
 *
 * 本文件做三件事：
 *   1. 用 ops 函数表把 init/start/stop/pause/resume 等抽象调用转发到 vendor 层；
 *   2. 把 vendor 层的采样回调桥接成 platform/service 层的回调；
 *   3. 承接启动文件里的中断向量（ADC/DMA/TIM/EXTI），再转发给 vendor 层。
 *
 * 中断转发关系：
 *   ADC_IRQHandler          -> vendor_stm32_encoder_adc_adc_irq_handler
 *   DMA2_Stream0_IRQHandler -> vendor_stm32_encoder_adc_dma_irq_handler（采样主通路）
 *   TIM2_IRQHandler         -> vendor_stm32_encoder_adc_tim_irq_handler（当前未启用）
 *   TIM3_IRQHandler         -> 旧版兼容入口（当前未启用）
 *   EXTI15_10_IRQHandler    -> vendor_stm32_encoder_adc_z_irq_handler（Z 相测速）
 */

#include "impl_stm32_encoder_adc.h"
#include "vendor_stm32_encoder_adc.h"

/**
 * @brief STM32 编码器触发 ADC 的实现层对象。
 *
 * base 必须放在第一个成员，方便把 impl_stm32_encoder_adc_t 当作
 * platform_adc_stream_t 使用，这是 C 语言实现“继承”的常见写法。
 *
 * @param base 对外暴露的 platform ADC 采样流基类对象。
 * @param state 当前实现对象状态。
 * @param sample_callback service 注册的采样回调。
 * @param sample_context service 注册回调时传入的上下文。
 */
typedef struct {
    platform_adc_stream_t base;
    platform_adc_stream_state_t state;
    platform_adc_stream_sample_callback_t sample_callback;
    void *sample_context;
} impl_stm32_encoder_adc_t;

static platform_status_t impl_stm32_encoder_adc_init(platform_adc_stream_t *stream,
                                                     const platform_adc_stream_config_t *config);
static platform_status_t impl_stm32_encoder_adc_start(platform_adc_stream_t *stream);
static platform_status_t impl_stm32_encoder_adc_stop(platform_adc_stream_t *stream);
static platform_status_t impl_stm32_encoder_adc_pause(platform_adc_stream_t *stream);
static platform_status_t impl_stm32_encoder_adc_resume(platform_adc_stream_t *stream);
static platform_status_t impl_stm32_encoder_adc_set_sample_callback(platform_adc_stream_t *stream,
                                                                    platform_adc_stream_sample_callback_t callback,
                                                                    void *context);
static platform_status_t impl_stm32_encoder_adc_get_speed_update(platform_adc_stream_t *stream,
                                                                 platform_adc_stream_speed_t *speed);
static platform_adc_stream_state_t impl_stm32_encoder_adc_get_state(platform_adc_stream_t *stream);

/**
 * @brief platform_adc_stream_ops_t 操作表实例。
 *
 * service 层最终通过 base.ops 调用这里的函数，实现从 platform 抽象到 STM32 vendor 的分发。
 */
static const platform_adc_stream_ops_t impl_stm32_encoder_adc_ops = {
    impl_stm32_encoder_adc_init,
    impl_stm32_encoder_adc_start,
    impl_stm32_encoder_adc_stop,
    impl_stm32_encoder_adc_pause,
    impl_stm32_encoder_adc_resume,
    impl_stm32_encoder_adc_set_sample_callback,
    impl_stm32_encoder_adc_get_speed_update,
    impl_stm32_encoder_adc_get_state
};

static impl_stm32_encoder_adc_t impl_stm32_encoder_adc;

/**
 * @brief 只在第一次使用时绑定 base.ops 和 base.impl。
 *
 * @note 该函数让静态对象具备 platform_adc_stream_t 的多态入口。
 */
static void impl_stm32_encoder_adc_bind(void)
{
    if (impl_stm32_encoder_adc.base.ops == 0) {
        impl_stm32_encoder_adc.base.ops = &impl_stm32_encoder_adc_ops;
        impl_stm32_encoder_adc.base.impl = &impl_stm32_encoder_adc;
        impl_stm32_encoder_adc.state = PLATFORM_ADC_STREAM_UNINIT;
    }
}

/**
 * @brief 从 platform 基类指针取回实现层私有对象。
 *
 * @param stream platform 层传入的 ADC 采样流对象。
 * @return 成功时返回 impl_stm32_encoder_adc_t 指针，参数为空时返回 0。
 */
static impl_stm32_encoder_adc_t *impl_stm32_encoder_adc_from_stream(platform_adc_stream_t *stream)
{
    if (stream == 0) {
        return 0;
    }

    return (impl_stm32_encoder_adc_t *)stream->impl;
}

/**
 * @brief vendor 层采样回调到 platform/service 回调的桥接函数。
 *
 * @param samples vendor 层送出的 8bit ADC 原始采样数组。
 * @param sample_count 本次数组中的有效采样数。
 * @param context 注册到 vendor 层的 impl_stm32_encoder_adc_t 对象指针。
 */
static void impl_stm32_encoder_adc_vendor_sample(const uint8_t *samples,
                                                 uint32_t sample_count,
                                                 void *context)
{
    impl_stm32_encoder_adc_t *self = (impl_stm32_encoder_adc_t *)context;

    if ((self == 0) || (self->state != PLATFORM_ADC_STREAM_RUNNING) ||
        (self->sample_callback == 0) ||
        ((samples == 0) && (sample_count > 0U))) {
        return;
    }

    self->sample_callback(&self->base,
                          samples,
                          sample_count,
                          self->sample_context);
}

/**
 * @brief 初始化 STM32 编码器触发 ADC 实现对象。
 *
 * @param stream platform ADC 采样流对象。
 * @param config service 层传入的采样参数。
 * @return PLATFORM_OK 表示 vendor ADC/TIM/DMA 初始化成功。
 */
static platform_status_t impl_stm32_encoder_adc_init(platform_adc_stream_t *stream,
                                                     const platform_adc_stream_config_t *config)
{
    impl_stm32_encoder_adc_t *self = impl_stm32_encoder_adc_from_stream(stream);
    vendor_stm32_encoder_adc_config_t vendor_config;

    if ((self == 0) || (config == 0)) {
        return PLATFORM_INVALID_PARAM;
    }

    vendor_config.trigger_counts_per_sample = config->trigger_counts_per_sample;
    vendor_config.speed_update_ms = config->speed_update_ms;
    vendor_config.encoder_counts_per_rev = config->encoder_counts_per_rev;
    vendor_config.irq_preempt_priority = 1U;
    vendor_config.irq_sub_priority = 2U;

    vendor_stm32_encoder_adc_set_sample_handler(impl_stm32_encoder_adc_vendor_sample, self);

    if (vendor_stm32_encoder_adc_init(&vendor_config) != VENDOR_STM32_ENCODER_ADC_OK) {
        self->state = PLATFORM_ADC_STREAM_ERROR;
        return PLATFORM_ERROR;
    }

    self->state = PLATFORM_ADC_STREAM_READY;
    return PLATFORM_OK;
}

/**
 * @brief 启动底层编码器触发 ADC 采样。
 *
 * @param stream platform ADC 采样流对象。
 * @return PLATFORM_OK 表示已经进入 RUNNING 状态。
 */
static platform_status_t impl_stm32_encoder_adc_start(platform_adc_stream_t *stream)
{
    impl_stm32_encoder_adc_t *self = impl_stm32_encoder_adc_from_stream(stream);

    if (self == 0) {
        return PLATFORM_INVALID_PARAM;
    }

    if (self->state == PLATFORM_ADC_STREAM_UNINIT) {
        return PLATFORM_NOT_READY;
    }

    vendor_stm32_encoder_adc_start();
    self->state = PLATFORM_ADC_STREAM_RUNNING;
    return PLATFORM_OK;
}

/**
 * @brief 停止底层编码器触发 ADC 采样。
 *
 * @param stream platform ADC 采样流对象。
 * @return PLATFORM_OK 表示已经回到 READY 状态。
 */
static platform_status_t impl_stm32_encoder_adc_stop(platform_adc_stream_t *stream)
{
    impl_stm32_encoder_adc_t *self = impl_stm32_encoder_adc_from_stream(stream);

    if (self == 0) {
        return PLATFORM_INVALID_PARAM;
    }

    if (self->state == PLATFORM_ADC_STREAM_UNINIT) {
        return PLATFORM_NOT_READY;
    }

    vendor_stm32_encoder_adc_stop();
    self->state = PLATFORM_ADC_STREAM_READY;
    return PLATFORM_OK;
}

/**
 * @brief 暂停新的 ADC 采样，同时保持 TIM2/TIM5 转速计数连续。
 *
 * @param stream platform ADC 采样流对象。
 * @return PLATFORM_OK 表示已经进入 PAUSED 状态。
 */
static platform_status_t impl_stm32_encoder_adc_pause(platform_adc_stream_t *stream)
{
    impl_stm32_encoder_adc_t *self = impl_stm32_encoder_adc_from_stream(stream);

    if (self == 0) {
        return PLATFORM_INVALID_PARAM;
    }

    if (self->state != PLATFORM_ADC_STREAM_RUNNING) {
        return PLATFORM_NOT_READY;
    }

    vendor_stm32_encoder_adc_pause();
    self->state = PLATFORM_ADC_STREAM_PAUSED;
    return PLATFORM_OK;
}

/**
 * @brief 恢复 pause 后的 ADC 采样。
 *
 * @param stream platform ADC 采样流对象。
 * @return PLATFORM_OK 表示已经重新进入 RUNNING 状态。
 */
static platform_status_t impl_stm32_encoder_adc_resume(platform_adc_stream_t *stream)
{
    impl_stm32_encoder_adc_t *self = impl_stm32_encoder_adc_from_stream(stream);

    if (self == 0) {
        return PLATFORM_INVALID_PARAM;
    }

    if (self->state != PLATFORM_ADC_STREAM_PAUSED) {
        return PLATFORM_NOT_READY;
    }

    vendor_stm32_encoder_adc_resume();
    self->state = PLATFORM_ADC_STREAM_RUNNING;
    return PLATFORM_OK;
}

/**
 * @brief 保存 service 注册的 ADC 采样回调。
 *
 * @param stream platform ADC 采样流对象。
 * @param callback 采样字节到达时要调用的 service 回调。
 * @param context 回调上下文，通常是 adc_stream_service_t。
 * @return PLATFORM_OK 表示保存成功。
 */
static platform_status_t impl_stm32_encoder_adc_set_sample_callback(platform_adc_stream_t *stream,
                                                                    platform_adc_stream_sample_callback_t callback,
                                                                    void *context)
{
    impl_stm32_encoder_adc_t *self = impl_stm32_encoder_adc_from_stream(stream);

    if (self == 0) {
        return PLATFORM_INVALID_PARAM;
    }

    self->sample_callback = callback;
    self->sample_context = context;
    return PLATFORM_OK;
}

/**
 * @brief 从 vendor 层读取最近一次转速更新并转换成 platform 数据结构。
 *
 * @param stream platform ADC 采样流对象。
 * @param speed 输出参数，保存 rpm 和 updated 标志。
 * @return PLATFORM_OK 表示读取成功。
 */
static platform_status_t impl_stm32_encoder_adc_get_speed_update(platform_adc_stream_t *stream,
                                                                 platform_adc_stream_speed_t *speed)
{
    vendor_stm32_encoder_adc_speed_t vendor_speed;

    if ((impl_stm32_encoder_adc_from_stream(stream) == 0) || (speed == 0)) {
        return PLATFORM_INVALID_PARAM;
    }

    if (vendor_stm32_encoder_adc_get_speed_update(&vendor_speed) != VENDOR_STM32_ENCODER_ADC_OK) {
        return PLATFORM_ERROR;
    }

    speed->rpm = vendor_speed.rpm;
    speed->updated = vendor_speed.updated;
    return PLATFORM_OK;
}

/**
 * @brief 获取实现对象当前状态。
 *
 * @param stream platform ADC 采样流对象。
 * @return 当前 platform_adc_stream_state_t 状态值。
 */
static platform_adc_stream_state_t impl_stm32_encoder_adc_get_state(platform_adc_stream_t *stream)
{
    impl_stm32_encoder_adc_t *self = impl_stm32_encoder_adc_from_stream(stream);

    if (self == 0) {
        return PLATFORM_ADC_STREAM_ERROR;
    }

    return self->state;
}

/**
 * @brief 获取 STM32 编码器触发 ADC 的 platform 对象。
 *
 * @return 指向静态 base 对象的指针，service 通过它调用 ops。
 */
platform_adc_stream_t *impl_stm32_encoder_adc_get_stream(void)
{
    impl_stm32_encoder_adc_bind();
    return &impl_stm32_encoder_adc.base;
}

/**
 * @brief ADC1 中断处理转接函数。
 *
 * 实际清标志和错误处理放在 vendor 层。
 */
void impl_stm32_encoder_adc_adc_irq_handler(void)
{
    vendor_stm32_encoder_adc_adc_irq_handler();
}

/**
 * @brief DMA2_Stream0 中断处理转接函数。
 *
 * vendor 层会在半满/满中断中把 DMA 缓冲区采样值送入回调链。
 */
void impl_stm32_encoder_adc_dma_irq_handler(void)
{
    vendor_stm32_encoder_adc_dma_irq_handler();
}

/**
 * @brief TIM2 编码器更新中断转接函数。
 *
 * vendor 层在这里累计编码器计数，用于后续转速计算。
 */
void impl_stm32_encoder_adc_tim_irq_handler(void)
{
    vendor_stm32_encoder_adc_tim_irq_handler();
}

/**
 * @brief 旧版 TIM3 兼容入口；当前版本使用 Z 相测速，不再使用该中断。
 *
 * vendor 层在这里把累计编码器计数换算成 rpm。
 */
void impl_stm32_encoder_adc_speed_irq_handler(void)
{
    vendor_stm32_encoder_adc_speed_irq_handler();
}

/** @brief 把 PB12 Z 相 EXTI 中断转发给 vendor 层处理。 */
void impl_stm32_encoder_adc_z_irq_handler(void)
{
    vendor_stm32_encoder_adc_z_irq_handler();
}

/**
 * @brief 启动文件引用的 ADC1_IRQHandler。
 *
 * 保持中断符号在 implement 层，内部再转到 vendor 层处理。
 */
void ADC_IRQHandler(void)
{
    impl_stm32_encoder_adc_adc_irq_handler();
}

/**
 * @brief 启动文件引用的 DMA2_Stream0_IRQHandler。
 */
void DMA2_Stream0_IRQHandler(void)
{
    impl_stm32_encoder_adc_dma_irq_handler();
}

/**
 * @brief 启动文件引用的 TIM2_IRQHandler。
 */
void TIM2_IRQHandler(void)
{
    impl_stm32_encoder_adc_tim_irq_handler();
}

/**
 * @brief 启动文件引用的旧版 TIM3 中断入口（当前版本未启用）。
 */
void TIM3_IRQHandler(void)
{
    impl_stm32_encoder_adc_speed_irq_handler();
}

/** @brief 启动文件引用的 PB12/EXTI12 Z 相脉冲中断入口。 */
void EXTI15_10_IRQHandler(void)
{
    impl_stm32_encoder_adc_z_irq_handler();
}
