#include "vendor_stm32_encoder_adc.h"
#include "stm32f4xx.h"
/* sys.h supplies INTX_DISABLE/INTX_ENABLE; armclang has no __disable_irq() intrinsic. */
#include "sys.h"

/* New board binding: PA5 is TIM2_CH1 single-edge input, PA4 is phase-A GPIO. */
#define VENDOR_ADC_GPIO_PORT               GPIOA
#define VENDOR_ADC_PHASE_A_PIN             GPIO_Pin_4
#define VENDOR_ADC_PHASE_B_PIN             GPIO_Pin_5
#define VENDOR_ADC_ANALOG_PIN              GPIO_Pin_2
#define VENDOR_ADC_CHANNEL                 ADC_Channel_2
#define VENDOR_ADC_SAMPLE_TIME             ADC_SampleTime_56Cycles
/* One PA5 rising edge produces one ADC trigger by default. */
#define VENDOR_ADC_DEFAULT_TRIGGER_COUNTS  1U
#define VENDOR_ADC_DEFAULT_SPEED_MS        500U
#define VENDOR_ADC_DEFAULT_COUNTS_PER_REV  4000U
/* ADC DMA 环形缓冲区大小，一半大小对应一次半满/满中断搬运量。 */
#define VENDOR_ADC_DMA_BUFFER_SIZE         1024U
#define VENDOR_ADC_DMA_HALF_SIZE           (VENDOR_ADC_DMA_BUFFER_SIZE / 2U)
#define VENDOR_ADC_DMA_IRQ_PREEMPT_PRIORITY 0U
#define VENDOR_ADC_DMA_IRQ_SUB_PRIORITY     0U
/* TIM5 is a 1 MHz free-running timestamp for the once-per-revolution Z edge. */
#define VENDOR_TIM5_INPUT_CLOCK_HZ         72000000U
#define VENDOR_TIM5_TICK_HZ                1000000U
#define VENDOR_TIM5_PRESCALER              (VENDOR_TIM5_INPUT_CLOCK_HZ / VENDOR_TIM5_TICK_HZ)
#define VENDOR_Z_MAX_VALID_RPM             12000U
#define VENDOR_Z_MIN_PERIOD_TICKS          ((60U * VENDOR_TIM5_TICK_HZ) / VENDOR_Z_MAX_VALID_RPM)

static vendor_stm32_encoder_adc_sample_handler_t vendor_adc_sample_handler;
static void *vendor_adc_sample_context;
static uint16_t vendor_adc_trigger_counts = VENDOR_ADC_DEFAULT_TRIGGER_COUNTS;
static uint16_t vendor_adc_speed_update_ms = VENDOR_ADC_DEFAULT_SPEED_MS;
static uint16_t vendor_adc_dma_buffer[VENDOR_ADC_DMA_BUFFER_SIZE];
static uint8_t vendor_adc_packed_samples[VENDOR_ADC_DMA_HALF_SIZE];
static volatile uint8_t vendor_adc_acquisition_active;
/*
 * Index of the first DMA sample that has not been submitted to the service
 * layer yet. NDTR tells us where DMA stopped writing; this cursor tells us
 * where the unsent tail really begins.
 */
static volatile uint32_t vendor_adc_dma_emit_position;
static volatile uint32_t vendor_adc_latest_rpm;
static volatile uint8_t vendor_adc_speed_updated;
static volatile uint32_t vendor_adc_z_last_tick;
static volatile uint32_t vendor_adc_z_last_report_tick;
static volatile uint32_t vendor_adc_speed_timeout_ticks;
static volatile uint8_t vendor_adc_z_reference_valid;
static volatile uint32_t vendor_adc_debug_dma_half_irq_count;
static volatile uint32_t vendor_adc_debug_dma_full_irq_count;
static volatile uint32_t vendor_adc_debug_samples_emitted;
static volatile uint32_t vendor_adc_debug_z_irq_count;
static volatile uint32_t vendor_adc_debug_last_z_period_us;
static volatile uint32_t vendor_adc_debug_dma_transfer_error_count;
static volatile uint32_t vendor_adc_debug_dma_direct_error_count;
static volatile uint32_t vendor_adc_debug_dma_fifo_error_count;
static volatile uint8_t vendor_adc_debug_min;
static volatile uint8_t vendor_adc_debug_max;
static volatile uint8_t vendor_adc_debug_last;
static volatile uint8_t vendor_adc_debug_sample_seen;

static void vendor_adc_reset_debug_counters(void)
{
    vendor_adc_debug_dma_half_irq_count = 0U;
    vendor_adc_debug_dma_full_irq_count = 0U;
    vendor_adc_debug_samples_emitted = 0U;
    vendor_adc_debug_z_irq_count = 0U;
    vendor_adc_debug_last_z_period_us = 0U;
    vendor_adc_debug_dma_transfer_error_count = 0U;
    vendor_adc_debug_dma_direct_error_count = 0U;
    vendor_adc_debug_dma_fifo_error_count = 0U;
    vendor_adc_debug_min = 0xFFU;
    vendor_adc_debug_max = 0U;
    vendor_adc_debug_last = 0U;
    vendor_adc_debug_sample_seen = 0U;
}

/**
 * @brief 归一化“多少个编码器计数触发一次 ADC 采样”参数。
 *
 * @param trigger_counts 上层配置的触发计数；传 0 时使用默认值。
 * @return 实际用于 TIM2 周期的触发计数。
 */
static uint16_t vendor_adc_normalize_trigger_counts(uint16_t trigger_counts)
{
    if (trigger_counts == 0U) {
        return VENDOR_ADC_DEFAULT_TRIGGER_COUNTS;
    }

    return trigger_counts;
}

/**
 * @brief 归一化转速统计周期参数。
 *
 * @param speed_update_ms 上层配置的统计周期，单位 ms；传 0 时使用默认值。
 * @return 实际用于 TIM3 的统计周期，单位 ms。
 */
static uint16_t vendor_adc_normalize_speed_ms(uint16_t speed_update_ms)
{
    if (speed_update_ms == 0U) {
        return VENDOR_ADC_DEFAULT_SPEED_MS;
    }

    return speed_update_ms;
}

/**
 * @brief 等待 DMA2 Stream0 真正关闭。
 *
 * STM32 DMA 禁用不是瞬时完成，重新配置或重启前必须等待硬件状态变为 DISABLE。
 */
static void vendor_adc_wait_dma_disabled(void)
{
    while (DMA_GetCmdStatus(DMA2_Stream0) != DISABLE) {
    }
}

/**
 * @brief 清除 DMA2 Stream0 所有相关挂起标志。
 *
 * 包含半传输、传输完成、传输错误、直接模式错误和 FIFO 错误标志。
 */
static void vendor_adc_clear_dma_pending(void)
{
    DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_HTIF0);
    DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_TCIF0);
    DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_TEIF0);
    DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_DMEIF0);
    DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_FEIF0);
}

/**
 * @brief 从 DMA 缓冲区取出一段采样并通过回调上送。
 *
 * @param source_offset DMA 缓冲区起始下标，0 表示前半区，VENDOR_ADC_DMA_HALF_SIZE 表示后半区。
 * @param sample_count 要上送的采样数量。
 *
 * ADC 配置为 8bit 分辨率，但 DMA 缓冲区用 uint16_t 承接 ADC_DR，因此这里只取低 8 位。
 */
static void vendor_adc_emit_dma_samples(uint32_t source_offset, uint32_t sample_count)
{
    uint32_t i;

    if ((vendor_adc_acquisition_active == 0U) || (vendor_adc_sample_handler == 0)) {
        return;
    }

    if (sample_count > VENDOR_ADC_DMA_HALF_SIZE) {
        sample_count = VENDOR_ADC_DMA_HALF_SIZE;
    }

    for (i = 0U; i < sample_count; i++) {
        vendor_adc_packed_samples[i] =
            (uint8_t)(vendor_adc_dma_buffer[source_offset + i] & 0xFFU);
        vendor_adc_debug_last = vendor_adc_packed_samples[i];
        if (vendor_adc_packed_samples[i] < vendor_adc_debug_min) {
            vendor_adc_debug_min = vendor_adc_packed_samples[i];
        }
        if (vendor_adc_packed_samples[i] > vendor_adc_debug_max) {
            vendor_adc_debug_max = vendor_adc_packed_samples[i];
        }
    }
    vendor_adc_debug_sample_seen = 1U;
    vendor_adc_debug_samples_emitted += sample_count;

    vendor_adc_sample_handler(vendor_adc_packed_samples,
                              sample_count,
                              vendor_adc_sample_context);
}

/**
 * @brief 停止采样前处理尚未清除的 DMA 半满/满中断数据。
 *
 * stop 时先把已经完成的半区数据送出，避免停止瞬间丢掉可用采样。
 */
static void vendor_adc_emit_dma_range(uint32_t source_offset, uint32_t sample_count)
{
    uint32_t contiguous_samples;
    uint32_t chunk_samples;

    source_offset %= VENDOR_ADC_DMA_BUFFER_SIZE;

    while (sample_count > 0U) {
        contiguous_samples = VENDOR_ADC_DMA_BUFFER_SIZE - source_offset;
        chunk_samples = sample_count;

        if (chunk_samples > contiguous_samples) {
            chunk_samples = contiguous_samples;
        }
        if (chunk_samples > VENDOR_ADC_DMA_HALF_SIZE) {
            chunk_samples = VENDOR_ADC_DMA_HALF_SIZE;
        }

        vendor_adc_emit_dma_samples(source_offset, chunk_samples);
        source_offset = (source_offset + chunk_samples) % VENDOR_ADC_DMA_BUFFER_SIZE;
        sample_count -= chunk_samples;
    }
}

/*
 * Submit every sample from the saved emit cursor up to write_position.
 * Both the normal IRQ path and the stop path advance this same cursor, so a
 * DMA sample range cannot be submitted twice at acquisition stop.
 */
static void vendor_adc_emit_until(uint32_t write_position)
{
    uint32_t sample_count;

    write_position %= VENDOR_ADC_DMA_BUFFER_SIZE;

    if (write_position >= vendor_adc_dma_emit_position) {
        sample_count = write_position - vendor_adc_dma_emit_position;
    } else {
        sample_count = (VENDOR_ADC_DMA_BUFFER_SIZE - vendor_adc_dma_emit_position) +
                       write_position;
    }

    if (sample_count > 0U) {
        vendor_adc_emit_dma_range(vendor_adc_dma_emit_position, sample_count);
        vendor_adc_dma_emit_position = write_position;
    }
}

/**
 * @brief 配置 GPIOA0/A1 为 TIM2 编码器输入，GPIOA2 为 ADC 模拟输入。
 */
static void vendor_adc_config_gpio(void)
{
    GPIO_InitTypeDef gpio;

    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA | RCC_AHB1Periph_GPIOB, ENABLE);

    /* PA5 is TIM2_CH1. Only its rising edge advances the sample divider. */
    gpio.GPIO_Pin = VENDOR_ADC_PHASE_B_PIN;
    gpio.GPIO_Mode = GPIO_Mode_AF;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(VENDOR_ADC_GPIO_PORT, &gpio);
    GPIO_PinAFConfig(GPIOA, GPIO_PinSource5, GPIO_AF_TIM2);

    /* PA4 remains available as a phase-A level input, but it is not timer-capable. */
    gpio.GPIO_Pin = VENDOR_ADC_PHASE_A_PIN;
    gpio.GPIO_Mode = GPIO_Mode_IN;
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(GPIOA, &gpio);

    gpio.GPIO_Pin = VENDOR_ADC_ANALOG_PIN;
    gpio.GPIO_Mode = GPIO_Mode_AIN;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(VENDOR_ADC_GPIO_PORT, &gpio);

    /* PB11 enables the new-board 3 V rail. Keep it enabled after initialization. */
    GPIO_ResetBits(GPIOB, GPIO_Pin_11);
    gpio.GPIO_Pin = GPIO_Pin_11;
    gpio.GPIO_Mode = GPIO_Mode_OUT;
    gpio.GPIO_Speed = GPIO_Speed_2MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(GPIOB, &gpio);
    GPIO_SetBits(GPIOB, GPIO_Pin_11);

    /* PB12 receives the active-low Z pulse from U8. */
    gpio.GPIO_Pin = GPIO_Pin_12;
    gpio.GPIO_Mode = GPIO_Mode_IN;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(GPIOB, &gpio);
}

/**
 * @brief Route each PA5 rising edge through TIM2_CH1/TRGO to the ADC trigger.
 *
 * @param trigger_counts 编码器累计多少个计数产生一次更新事件/TRGO。
 *
 * Channel 1 directly captures TI1/PA5.  TIM2 compare-pulse TRGO emits a pulse
 * whenever CC1IF is set, including an input-capture event.  The
 * trigger_counts argument is kept for interface compatibility; this board
 * uses one capture per rising edge.
 */
static void vendor_adc_config_tim2_single_edge(uint16_t trigger_counts)
{
    TIM_TimeBaseInitTypeDef time_base;
    TIM_ICInitTypeDef input_capture;
    (void)trigger_counts;

    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM2, ENABLE);

    TIM_Cmd(TIM2, DISABLE);
    TIM_DeInit(TIM2);

    time_base.TIM_Prescaler = 0U;
    time_base.TIM_CounterMode = TIM_CounterMode_Up;
    time_base.TIM_Period = 0xFFFFFFFFU;
    time_base.TIM_ClockDivision = TIM_CKD_DIV1;
    time_base.TIM_RepetitionCounter = 0U;
    TIM_TimeBaseInit(TIM2, &time_base);

    /*
     * CH1 directly captures TI1 (PA5).  MMS=011 selects the compare-pulse
     * master output, so each CC1 input-capture event produces a TIM2_TRGO
     * pulse for ADC1.  TIM2 itself free-runs; PA5 is an input event, not its
     * counter clock.
     */
    TIM_InternalClockConfig(TIM2);
    TIM_ICStructInit(&input_capture);
    input_capture.TIM_Channel = TIM_Channel_1;
    input_capture.TIM_ICPolarity = TIM_ICPolarity_Rising;
    input_capture.TIM_ICSelection = TIM_ICSelection_DirectTI;
    input_capture.TIM_ICPrescaler = TIM_ICPSC_DIV1;
    input_capture.TIM_ICFilter = 0U;
    TIM_ICInit(TIM2, &input_capture);
    TIM_SelectOutputTrigger(TIM2, TIM_TRGOSource_OC1);

    TIM_ClearFlag(TIM2, TIM_FLAG_CC1 | TIM_FLAG_CC1OF | TIM_FLAG_Update);
    TIM_ITConfig(TIM2, TIM_IT_CC1 | TIM_IT_Update, DISABLE);
    NVIC_DisableIRQ(TIM2_IRQn);
    TIM_SetCounter(TIM2, 0U);
}

/**
 * @brief Configure TIM5 as a 1 MHz, 32-bit free-running Z timestamp.
 */
static void vendor_adc_config_tim5_timestamp(void)
{
    TIM_TimeBaseInitTypeDef time_base;

    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM5, ENABLE);

    TIM_Cmd(TIM5, DISABLE);
    TIM_DeInit(TIM5);

    time_base.TIM_Prescaler = VENDOR_TIM5_PRESCALER - 1U;
    time_base.TIM_CounterMode = TIM_CounterMode_Up;
    time_base.TIM_Period = 0xFFFFFFFFU;
    time_base.TIM_ClockDivision = TIM_CKD_DIV1;
    time_base.TIM_RepetitionCounter = 0U;
    TIM_TimeBaseInit(TIM5, &time_base);
    TIM_SetCounter(TIM5, 0U);
}

/** Configure PB12 falling-edge EXTI for the active-low Z index pulse. */
static void vendor_adc_config_z_irq(const vendor_stm32_encoder_adc_config_t *config)
{
    EXTI_InitTypeDef exti;
    NVIC_InitTypeDef nvic;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_SYSCFG, ENABLE);
    SYSCFG_EXTILineConfig(EXTI_PortSourceGPIOB, EXTI_PinSource12);

    EXTI_ClearITPendingBit(EXTI_Line12);
    exti.EXTI_Line = EXTI_Line12;
    exti.EXTI_Mode = EXTI_Mode_Interrupt;
    exti.EXTI_Trigger = EXTI_Trigger_Falling;
    exti.EXTI_LineCmd = ENABLE;
    EXTI_Init(&exti);

    nvic.NVIC_IRQChannel = EXTI15_10_IRQn;
    nvic.NVIC_IRQChannelPreemptionPriority =
        (uint8_t)(config->irq_preempt_priority + 1U);
    nvic.NVIC_IRQChannelSubPriority =
        (uint8_t)(config->irq_sub_priority + 1U);
    nvic.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&nvic);
}

/**
 * @brief 配置 ADC1 使用的 DMA2 Stream0 环形搬运。
 *
 * @param config 初始化配置，使用其中的中断优先级字段。
 *
 * DMA 半满和满中断分别对应前半/后半缓冲区采样上送。
 */
static void vendor_adc_config_dma_irq(void)
{
    NVIC_InitTypeDef nvic;

    nvic.NVIC_IRQChannel = DMA2_Stream0_IRQn;
    nvic.NVIC_IRQChannelPreemptionPriority =
        VENDOR_ADC_DMA_IRQ_PREEMPT_PRIORITY;
    nvic.NVIC_IRQChannelSubPriority = VENDOR_ADC_DMA_IRQ_SUB_PRIORITY;
    nvic.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&nvic);
}

static void vendor_adc_config_dma(void)
{
    DMA_InitTypeDef dma;

    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_DMA2, ENABLE);
    /*
     * USB also uses PriorityGroup_1. Configure the group before programming
     * the DMA priority so preemption priority 0 is encoded as intended.
     */
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_1);

    DMA_Cmd(DMA2_Stream0, DISABLE);
    vendor_adc_wait_dma_disabled();
    DMA_DeInit(DMA2_Stream0);
    vendor_adc_clear_dma_pending();

    dma.DMA_Channel = DMA_Channel_0;
    dma.DMA_PeripheralBaseAddr = (uint32_t)&ADC1->DR;
    dma.DMA_Memory0BaseAddr = (uint32_t)vendor_adc_dma_buffer;
    dma.DMA_DIR = DMA_DIR_PeripheralToMemory;
    dma.DMA_BufferSize = VENDOR_ADC_DMA_BUFFER_SIZE;
    dma.DMA_PeripheralInc = DMA_PeripheralInc_Disable;
    dma.DMA_MemoryInc = DMA_MemoryInc_Enable;
    dma.DMA_PeripheralDataSize = DMA_PeripheralDataSize_HalfWord;
    dma.DMA_MemoryDataSize = DMA_MemoryDataSize_HalfWord;
    dma.DMA_Mode = DMA_Mode_Circular;
    dma.DMA_Priority = DMA_Priority_VeryHigh;
    dma.DMA_FIFOMode = DMA_FIFOMode_Disable;
    dma.DMA_FIFOThreshold = DMA_FIFOThreshold_HalfFull;
    dma.DMA_MemoryBurst = DMA_MemoryBurst_Single;
    dma.DMA_PeripheralBurst = DMA_PeripheralBurst_Single;
    DMA_Init(DMA2_Stream0, &dma);
    DMA_ITConfig(DMA2_Stream0, DMA_IT_HT | DMA_IT_TC | DMA_IT_TE | DMA_IT_DME, ENABLE);

    vendor_adc_config_dma_irq();
}

/**
 * @brief 配置 ADC1 为 TIM2_TRGO 上升沿触发的单通道 8bit 采样。
 *
 * ADC1_CH2 连接 GPIOA2，采样结果由 DMA 自动搬到 vendor_adc_dma_buffer。
 */
static void vendor_adc_config_adc1(void)
{
    ADC_CommonInitTypeDef common;
    ADC_InitTypeDef adc;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_ADC1, ENABLE);

    ADC_DeInit();

    common.ADC_Mode = ADC_Mode_Independent;
    common.ADC_Prescaler = ADC_Prescaler_Div2;
    common.ADC_DMAAccessMode = ADC_DMAAccessMode_Disabled;
    common.ADC_TwoSamplingDelay = ADC_TwoSamplingDelay_5Cycles;
    ADC_CommonInit(&common);

    adc.ADC_Resolution = ADC_Resolution_8b;
    adc.ADC_ScanConvMode = DISABLE;
    adc.ADC_ContinuousConvMode = DISABLE;
    adc.ADC_ExternalTrigConvEdge = ADC_ExternalTrigConvEdge_Rising;
    adc.ADC_ExternalTrigConv = ADC_ExternalTrigConv_T2_TRGO;
    adc.ADC_DataAlign = ADC_DataAlign_Right;
    adc.ADC_NbrOfConversion = 1U;
    ADC_Init(ADC1, &adc);

    ADC_RegularChannelConfig(ADC1, VENDOR_ADC_CHANNEL, 1U, VENDOR_ADC_SAMPLE_TIME);
    ADC_ITConfig(ADC1, ADC_IT_EOC, DISABLE);
    ADC_ITConfig(ADC1, ADC_IT_OVR, DISABLE);
    ADC_ClearITPendingBit(ADC1, ADC_IT_EOC);
    ADC_ClearITPendingBit(ADC1, ADC_IT_OVR);
    ADC_DMARequestAfterLastTransferCmd(ADC1, ENABLE);
    ADC_DMACmd(ADC1, ENABLE);
}

/**
 * @brief 初始化编码器触发 ADC 的全部 STM32 外设。
 *
 * @param config 采样触发计数、转速统计周期、编码器每转计数和中断优先级。
 * @return VENDOR_STM32_ENCODER_ADC_OK 表示 GPIO/TIM2/TIM3/TIM5/ADC/DMA 均已配置。
 */
vendor_stm32_encoder_adc_status_t vendor_stm32_encoder_adc_init(const vendor_stm32_encoder_adc_config_t *config)
{
    uint16_t speed_update_ms;

    if (config == 0) {
        return VENDOR_STM32_ENCODER_ADC_INVALID_PARAM;
    }

    vendor_adc_trigger_counts = vendor_adc_normalize_trigger_counts(config->trigger_counts_per_sample);
    speed_update_ms = vendor_adc_normalize_speed_ms(config->speed_update_ms);
    vendor_adc_acquisition_active = 0U;
    vendor_adc_dma_emit_position = 0U;
    vendor_adc_speed_update_ms = speed_update_ms;
    vendor_adc_latest_rpm = 0U;
    vendor_adc_speed_updated = 0U;
    vendor_adc_z_last_tick = 0U;
    vendor_adc_z_last_report_tick = 0U;
    vendor_adc_z_reference_valid = 0U;
    vendor_adc_reset_debug_counters();
    vendor_adc_speed_timeout_ticks = (uint32_t)speed_update_ms *
                                     (VENDOR_TIM5_TICK_HZ / 1000U);

    vendor_adc_config_gpio();
    vendor_adc_config_tim2_single_edge(vendor_adc_trigger_counts);
    vendor_adc_config_tim5_timestamp();
    vendor_adc_config_dma();
    vendor_adc_config_adc1();
    vendor_adc_config_z_irq(config);

    return VENDOR_STM32_ENCODER_ADC_OK;
}

/**
 * @brief 启动编码器触发 ADC 采样和转速统计。
 *
 * Start order is DMA -> ADC -> TIM5 -> TIM2, so storage, conversion and the
 * Z timestamp are ready before the first PA5 sampling edge arrives.
 */
void vendor_stm32_encoder_adc_start(void)
{
    TIM_Cmd(TIM2, DISABLE);
    TIM_Cmd(TIM5, DISABLE);
    ADC_Cmd(ADC1, DISABLE);
    DMA_Cmd(DMA2_Stream0, DISABLE);
    vendor_adc_wait_dma_disabled();

    vendor_adc_latest_rpm = 0U;
    vendor_adc_speed_updated = 0U;
    vendor_adc_z_last_tick = 0U;
    vendor_adc_z_last_report_tick = 0U;
    vendor_adc_z_reference_valid = 0U;
    vendor_adc_dma_emit_position = 0U;
    vendor_adc_acquisition_active = 1U;
    vendor_adc_reset_debug_counters();

    TIM_SetCounter(TIM2, 0U);
    TIM_SetCounter(TIM5, 0U);
    TIM_ClearFlag(TIM2, TIM_FLAG_CC1 | TIM_FLAG_CC1OF | TIM_FLAG_Update);
    EXTI_ClearITPendingBit(EXTI_Line12);
    NVIC_ClearPendingIRQ(EXTI15_10_IRQn);
    ADC_ClearITPendingBit(ADC1, ADC_IT_EOC);
    ADC_ClearITPendingBit(ADC1, ADC_IT_OVR);
    vendor_adc_clear_dma_pending();
    NVIC_ClearPendingIRQ(DMA2_Stream0_IRQn);
    DMA_SetCurrDataCounter(DMA2_Stream0, VENDOR_ADC_DMA_BUFFER_SIZE);

    /*
     * USB initialization runs after ADC initialization and programs the global
     * NVIC group. Reapply the DMA priority at each acquisition start so DMA
     * can always preempt the lower-priority USB FS interrupt.
     */
    vendor_adc_config_dma_irq();
    NVIC_EnableIRQ(DMA2_Stream0_IRQn);
    DMA_Cmd(DMA2_Stream0, ENABLE);
    ADC_Cmd(ADC1, ENABLE);
    TIM_Cmd(TIM5, ENABLE);
    TIM_Cmd(TIM2, ENABLE);
}

/**
 * @brief 停止采样和转速统计，并吐出 DMA 中已产生但未上送的采样。
 *
 * TIM2/TIM5/ADC/DMA are stopped before the pending DMA tail is emitted.
 */
void vendor_stm32_encoder_adc_stop(void)
{
    uint32_t write_position;
    uint8_t dma_boundary_pending;

    TIM_Cmd(TIM2, DISABLE);
    TIM_Cmd(TIM5, DISABLE);
    ADC_Cmd(ADC1, DISABLE);
    /*
     * Freeze the DMA IRQ first. Any completed-but-unhandled range is recovered
     * below from NDTR and the saved emit cursor.
     */
    NVIC_DisableIRQ(DMA2_Stream0_IRQn);
    DMA_Cmd(DMA2_Stream0, DISABLE);
    vendor_adc_wait_dma_disabled();

    if (vendor_adc_acquisition_active != 0U) {
        dma_boundary_pending =
            ((DMA_GetITStatus(DMA2_Stream0, DMA_IT_HTIF0) != RESET) ||
             (DMA_GetITStatus(DMA2_Stream0, DMA_IT_TCIF0) != RESET)) ? 1U : 0U;

        write_position =
            VENDOR_ADC_DMA_BUFFER_SIZE - DMA_GetCurrDataCounter(DMA2_Stream0);
        if (write_position >= VENDOR_ADC_DMA_BUFFER_SIZE) {
            write_position = 0U;
        }

        /*
         * Equal positions normally mean an empty tail. A pending boundary flag
         * means DMA completed one whole 1024-sample lap before its IRQ ran.
         */
        if ((write_position == vendor_adc_dma_emit_position) &&
            (dma_boundary_pending != 0U)) {
            vendor_adc_emit_dma_range(vendor_adc_dma_emit_position,
                                      VENDOR_ADC_DMA_BUFFER_SIZE);
        } else {
            vendor_adc_emit_until(write_position);
        }
    }

    vendor_adc_acquisition_active = 0U;
    vendor_adc_clear_dma_pending();
    NVIC_ClearPendingIRQ(DMA2_Stream0_IRQn);
}

/**
 * @brief 暂停新的 ADC 采样，但保持转速计数连续。
 *
 * 只关闭 ADC，TIM2/TIM5 继续运行，避免 USB 背压期间丢失转速计数。
 */
void vendor_stm32_encoder_adc_pause(void)
{
    ADC_Cmd(ADC1, DISABLE);
}

/**
 * @brief 恢复 pause 后的 ADC 采样。
 *
 * 恢复前清除 ADC 残留标志，TIM2/TIM5 的转速计数不中断。
 */
void vendor_stm32_encoder_adc_resume(void)
{
    ADC_ClearITPendingBit(ADC1, ADC_IT_EOC);
    ADC_ClearITPendingBit(ADC1, ADC_IT_OVR);
    ADC_Cmd(ADC1, ENABLE);
}

/**
 * @brief 注册采样字节上送回调。
 *
 * @param handler DMA 采到数据后调用的回调函数。
 * @param context 回调上下文，会原样传回 handler。
 */
void vendor_stm32_encoder_adc_set_sample_handler(vendor_stm32_encoder_adc_sample_handler_t handler,
                                                 void *context)
{
    vendor_adc_sample_handler = handler;
    vendor_adc_sample_context = context;
}

/**
 * @brief 读取最近一次转速统计结果。
 *
 * @param speed 输出参数，保存 rpm 和 updated 标志。
 * @return VENDOR_STM32_ENCODER_ADC_OK 表示读取成功。
 *
 * updated 是一次性标志，读取后会清零。
 */
vendor_stm32_encoder_adc_status_t vendor_stm32_encoder_adc_get_speed_update(vendor_stm32_encoder_adc_speed_t *speed)
{
    uint32_t now;

    if (speed == 0) {
        return VENDOR_STM32_ENCODER_ADC_INVALID_PARAM;
    }

    /*
     * Make the timeout check atomic with the Z ISR. Otherwise the interrupt
     * can update last_tick after foreground code reads now; unsigned
     * subtraction then looks like a huge timeout and briefly reports 0 RPM.
     */
    INTX_DISABLE();

    /* No Z edge for one report interval means the rotor is treated as stopped. */
    if ((vendor_adc_acquisition_active != 0U) &&
        (vendor_adc_z_reference_valid != 0U) &&
        (vendor_adc_latest_rpm != 0U) &&
        (vendor_adc_speed_timeout_ticks != 0U)) {
        now = TIM_GetCounter(TIM5);
        if ((uint32_t)(now - vendor_adc_z_last_tick) > vendor_adc_speed_timeout_ticks) {
            vendor_adc_latest_rpm = 0U;
            vendor_adc_speed_updated = 1U;
            vendor_adc_z_reference_valid = 0U;
        }
    }

    speed->rpm = vendor_adc_latest_rpm;
    speed->updated = vendor_adc_speed_updated;
    vendor_adc_speed_updated = 0U;

    INTX_ENABLE();

    return VENDOR_STM32_ENCODER_ADC_OK;
}

void vendor_stm32_encoder_adc_get_debug(vendor_stm32_encoder_adc_debug_t *debug)
{
    if (debug == 0) {
        return;
    }

    /* This foreground-only API is called with interrupts enabled. */
    INTX_DISABLE();

    debug->timestamp_us = TIM_GetCounter(TIM5);
    debug->rpm = vendor_adc_latest_rpm;
    debug->dma_half_irq_count = vendor_adc_debug_dma_half_irq_count;
    debug->dma_full_irq_count = vendor_adc_debug_dma_full_irq_count;
    debug->samples_emitted = vendor_adc_debug_samples_emitted;
    debug->z_irq_count = vendor_adc_debug_z_irq_count;
    debug->last_z_period_us = vendor_adc_debug_last_z_period_us;
    debug->dma_transfer_error_count = vendor_adc_debug_dma_transfer_error_count;
    debug->dma_direct_error_count = vendor_adc_debug_dma_direct_error_count;
    debug->dma_fifo_error_count = vendor_adc_debug_dma_fifo_error_count;
    debug->dma_remaining = (uint16_t)DMA_GetCurrDataCounter(DMA2_Stream0);
    debug->tim2_capture1 = TIM2->CCR1;
    debug->tim2_capture_seen = ((TIM2->SR & TIM_SR_CC1IF) != 0U) ? 1U : 0U;
    debug->tim2_capture_overflow = ((TIM2->SR & TIM_SR_CC1OF) != 0U) ? 1U : 0U;
    debug->adc_min = vendor_adc_debug_min;
    debug->adc_max = vendor_adc_debug_max;
    debug->adc_last = vendor_adc_debug_last;
    debug->adc_sample_seen = vendor_adc_debug_sample_seen;
    debug->adc_overrun = ((ADC1->SR & ADC_SR_OVR) != 0U) ? 1U : 0U;
    debug->acquisition_active = vendor_adc_acquisition_active;
    debug->adc_enabled = ((ADC1->CR2 & ADC_CR2_ADON) != 0U) ? 1U : 0U;
    debug->dma_enabled = ((DMA2_Stream0->CR & DMA_SxCR_EN) != 0U) ? 1U : 0U;
    debug->tim2_enabled = ((TIM2->CR1 & TIM_CR1_CEN) != 0U) ? 1U : 0U;
    debug->phase_b_level =
        (GPIO_ReadInputDataBit(GPIOA, VENDOR_ADC_PHASE_B_PIN) != Bit_RESET) ? 1U : 0U;

    INTX_ENABLE();
}

/**
 * @brief ADC1 中断处理函数。
 *
 * 当前 ADC 采样数据由 DMA 处理，这里只清除 EOC/OVR 标志，防止中断挂死。
 */
void vendor_stm32_encoder_adc_adc_irq_handler(void)
{
    if (ADC_GetITStatus(ADC1, ADC_IT_EOC) != RESET) {
        ADC_ClearITPendingBit(ADC1, ADC_IT_EOC);
    }

    if (ADC_GetITStatus(ADC1, ADC_IT_OVR) != RESET) {
        ADC_ClearITPendingBit(ADC1, ADC_IT_OVR);
    }
}

/**
 * @brief DMA2 Stream0 中断处理函数。
 *
 * 半满中断上送 DMA 缓冲区前半段，传输完成中断上送后半段；
 * 错误类标志这里只清除，后续如需统计错误可在这里扩展。
 */
void vendor_stm32_encoder_adc_dma_irq_handler(void)
{
    uint8_t half_pending =
        (DMA_GetITStatus(DMA2_Stream0, DMA_IT_HTIF0) != RESET) ? 1U : 0U;
    uint8_t complete_pending =
        (DMA_GetITStatus(DMA2_Stream0, DMA_IT_TCIF0) != RESET) ? 1U : 0U;

    if (half_pending != 0U) {
        vendor_adc_debug_dma_half_irq_count++;
        DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_HTIF0);
    }
    if (complete_pending != 0U) {
        vendor_adc_debug_dma_full_irq_count++;
        DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_TCIF0);
    }

    /*
     * If USB kept this IRQ waiting long enough for both flags to become set,
     * emit them in circular-buffer order. The saved cursor identifies which
     * boundary is expected next.
     */
    if (vendor_adc_dma_emit_position < VENDOR_ADC_DMA_HALF_SIZE) {
        if (half_pending != 0U) {
            vendor_adc_emit_until(VENDOR_ADC_DMA_HALF_SIZE);
        }
        if (complete_pending != 0U) {
            vendor_adc_emit_until(0U);
        }
    } else {
        if (complete_pending != 0U) {
            vendor_adc_emit_until(0U);
        }
        if (half_pending != 0U) {
            vendor_adc_emit_until(VENDOR_ADC_DMA_HALF_SIZE);
        }
    }

    if (DMA_GetITStatus(DMA2_Stream0, DMA_IT_TEIF0) != RESET) {
        vendor_adc_debug_dma_transfer_error_count++;
        DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_TEIF0);
    }

    if (DMA_GetITStatus(DMA2_Stream0, DMA_IT_DMEIF0) != RESET) {
        vendor_adc_debug_dma_direct_error_count++;
        DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_DMEIF0);
    }

    if (DMA_GetITStatus(DMA2_Stream0, DMA_IT_FEIF0) != RESET) {
        vendor_adc_debug_dma_fifo_error_count++;
        DMA_ClearITPendingBit(DMA2_Stream0, DMA_IT_FEIF0);
    }
}

/**
 * @brief 保留的 TIM2 更新中断转接函数。
 *
 * TIM2 CC2/update interrupts are disabled; CC2 triggers ADC directly in hardware.
 */
void vendor_stm32_encoder_adc_tim_irq_handler(void)
{
    TIM_ClearITPendingBit(TIM2, TIM_IT_Update);
}

/**
 * @brief Legacy TIM3 entry retained for startup-vector compatibility.
 */
void vendor_stm32_encoder_adc_speed_irq_handler(void)
{
}

/**
 * @brief PB12 Z-index interrupt: measure one revolution with TIM5 timestamps.
 *
 * U8 is wired as an active-low receiver in the current schematic, so EXTI12
 * is configured for the falling edge. The first edge only establishes the
 * reference. Every later valid edge updates the internal RPM value.
 */
void vendor_stm32_encoder_adc_z_irq_handler(void)
{
    uint32_t now;
    uint32_t period_ticks;

    if (EXTI_GetITStatus(EXTI_Line12) == RESET) {
        return;
    }

    EXTI_ClearITPendingBit(EXTI_Line12);
    if (vendor_adc_acquisition_active == 0U) {
        return;
    }

    vendor_adc_debug_z_irq_count++;

    now = TIM_GetCounter(TIM5);
    if (vendor_adc_z_reference_valid == 0U) {
        vendor_adc_z_last_tick = now;
        vendor_adc_z_last_report_tick = now;
        vendor_adc_z_reference_valid = 1U;
        return;
    }

    period_ticks = now - vendor_adc_z_last_tick;
    if (period_ticks < VENDOR_Z_MIN_PERIOD_TICKS) {
        return;
    }

    vendor_adc_z_last_tick = now;
    vendor_adc_debug_last_z_period_us = period_ticks;
    vendor_adc_latest_rpm = (60U * VENDOR_TIM5_TICK_HZ) / period_ticks;

    /* Calculate every revolution, but keep the existing USB report interval. */
    if ((uint32_t)(now - vendor_adc_z_last_report_tick) >=
        ((uint32_t)vendor_adc_speed_update_ms * (VENDOR_TIM5_TICK_HZ / 1000U))) {
        vendor_adc_z_last_report_tick = now;
        vendor_adc_speed_updated = 1U;
    }
}
