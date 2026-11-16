/**
 * @file vendor_stm32_encoder_adc.c
 * @brief 驱动层：编码器触发 ADC 采样、DMA 搬运与 Z 相测速（STM32F4 标准库）。
 *
 * 硬件通路：
 *   PA5(TIM2_CH1) 上升沿 -> TIM2 输入捕获 -> TRGO -> ADC1_IN2(PA2) -> DMA2_Stream0
 *   -> 半满/满中断 -> vendor_adc_emit_* -> IMPLEMENT 回调 -> SERVICE 队列；
 *   PB12 Z 相下降沿 -> EXTI12 -> 用 TIM5(1 MHz) 时间差计算 rpm。
 *
 * 关键设计：
 *   - TIM2 只负责触发：PA5 上升沿经输入捕获产生 TRGO，不产生 CPU 中断；
 *   - DMA 工作在循环模式，半满/满中断各提交半圈（512 个）采样；
 *   - vendor_adc_dma_emit_position 记录“已经提交给上层”的位置，
 *     正常中断和 stop 冲刷共用该游标，保证停止时不漏数据、不重复提交。
 *
 * 本文件直接操作寄存器/标准库外设，不依赖 PLATFORM/SERVICE。
 */

#include "vendor_stm32_encoder_adc.h"
#include "stm32f4xx.h"
/* sys.h 提供 INTX_DISABLE/INTX_ENABLE 宏；armclang 没有 __disable_irq() 内建函数。 */
#include "sys.h"

/* 新板卡引脚分配：PA5 为 TIM2_CH1 单边沿输入，PA4 为 A 相电平输入。 */
#define VENDOR_ADC_GPIO_PORT               GPIOA
#define VENDOR_ADC_PHASE_A_PIN             GPIO_Pin_4
#define VENDOR_ADC_PHASE_B_PIN             GPIO_Pin_5
#define VENDOR_ADC_ANALOG_PIN              GPIO_Pin_2
#define VENDOR_ADC_CHANNEL                 ADC_Channel_2
/*
 * ADC 采样时间：144 个采样周期 + 8 个转换周期 = 152 周期 @36 MHz = 4.22 µs，
 * 单次转换上限约 236.8 kSa/s。3000 rpm 时编码器周期 5 µs，余量约 0.78 µs。
 * 对应最高转速约 3552 rpm（理论；实际建议 ≤3300 rpm）。
 * 若后续提高探针转速，改回 112 周期（约 4500 rpm）或 56 周期（约 8437 rpm）。
 */
#define VENDOR_ADC_SAMPLE_TIME             ADC_SampleTime_144Cycles
/* 默认每个 PA5 上升沿产生 1 次 ADC 触发。 */
#define VENDOR_ADC_DEFAULT_TRIGGER_COUNTS  1U
#define VENDOR_ADC_DEFAULT_SPEED_MS        500U    /* 转速上报/停转超时周期：500 ms */
#define VENDOR_ADC_DEFAULT_COUNTS_PER_REV  4000U   /* 编码器每转计数（当前不参与 RPM 计算） */
/* ADC DMA 环形缓冲区大小，一半大小对应一次半满/满中断搬运量。 */
#define VENDOR_ADC_DMA_BUFFER_SIZE         1024U
#define VENDOR_ADC_DMA_HALF_SIZE           (VENDOR_ADC_DMA_BUFFER_SIZE / 2U)
#define VENDOR_ADC_DMA_IRQ_PREEMPT_PRIORITY 0U
#define VENDOR_ADC_DMA_IRQ_SUB_PRIORITY     0U
/* TIM5 作为 1 MHz 自由运行计数器，为每转一次的 Z 相边沿打时间戳。 */
#define VENDOR_TIM5_INPUT_CLOCK_HZ         72000000U
#define VENDOR_TIM5_TICK_HZ                1000000U
#define VENDOR_TIM5_PRESCALER              (VENDOR_TIM5_INPUT_CLOCK_HZ / VENDOR_TIM5_TICK_HZ)
#define VENDOR_Z_MAX_VALID_RPM             12000U    /* 转速上限，超过按无效处理 */
#define VENDOR_Z_MIN_PERIOD_TICKS          ((60U * VENDOR_TIM5_TICK_HZ) / VENDOR_Z_MAX_VALID_RPM) /* 12000 rpm 对应的最小周期 */

static vendor_stm32_encoder_adc_sample_handler_t vendor_adc_sample_handler;
static void *vendor_adc_sample_context;
static uint16_t vendor_adc_trigger_counts = VENDOR_ADC_DEFAULT_TRIGGER_COUNTS;
static uint16_t vendor_adc_speed_update_ms = VENDOR_ADC_DEFAULT_SPEED_MS;
static uint16_t vendor_adc_dma_buffer[VENDOR_ADC_DMA_BUFFER_SIZE];
static uint8_t vendor_adc_packed_samples[VENDOR_ADC_DMA_HALF_SIZE];
static volatile uint8_t vendor_adc_acquisition_active;
/*
 * 第一个尚未提交给 service 层的 DMA 采样下标。
 * NDTR（DMA 剩余计数）只能告诉我们 DMA 当前写到了哪里；
 * 这个游标才记录了“未上送尾部”真正从哪里开始。
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

/**
 * @brief 清零所有调试计数器。
 *
 * 每次新的采集开始时调用，保证 USART 遥测里的计数只反映本次采集。
 * 注意：最小值预置为 0xFF、最大值为 0，便于第一次比较时正确更新。
 */
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

/**
 * @brief 提交从“已上送游标”到 write_position 之间的全部采样。
 *
 * 正常中断路径和 stop 冲刷路径都会推进同一个游标，
 * 因此同一段 DMA 采样在停止采集时不会被重复提交。
 *
 * @param write_position DMA 当前写入位置（0 ~ VENDOR_ADC_DMA_BUFFER_SIZE-1）。
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

    /* PA5 为 TIM2_CH1，只有上升沿会推进采样分频计数。 */
    gpio.GPIO_Pin = VENDOR_ADC_PHASE_B_PIN;
    gpio.GPIO_Mode = GPIO_Mode_AF;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(VENDOR_ADC_GPIO_PORT, &gpio);
    GPIO_PinAFConfig(GPIOA, GPIO_PinSource5, GPIO_AF_TIM2);

    /* PA4 仍保留为 A 相电平输入，但该引脚不具备定时器输入功能。 */
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

    /* PB11 使能新板的 3 V 电源轨，初始化后保持使能。 */
    GPIO_ResetBits(GPIOB, GPIO_Pin_11);
    gpio.GPIO_Pin = GPIO_Pin_11;
    gpio.GPIO_Mode = GPIO_Mode_OUT;
    gpio.GPIO_Speed = GPIO_Speed_2MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(GPIOB, &gpio);
    GPIO_SetBits(GPIOB, GPIO_Pin_11);

    /* PB12 接收 U8 输出的低电平有效 Z 相脉冲。 */
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
     * CH1 直接捕获 TI1（PA5）。MMS=011 选择比较脉冲作为主输出，
     * 因此每次 CC1 输入捕获事件都会产生一个 TIM2_TRGO 脉冲送给 ADC1。
     * TIM2 本身自由运行；PA5 是输入事件，不作为计数器时钟。
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
 * @brief 把 TIM5 配置为 1 MHz、32 位自由运行计数器，用于 Z 相时间戳。
 *
 * 预分频 = 72 MHz / 1 MHz，周期设为最大值，计满后自然溢出回绕；
 * 计算时间差时使用无符号减法，可自动跨过溢出点。
 *
 * [NEW] TIM5 配置完成后立即启动，并且此后一直运行：
 * 采集开始时会重新清零，但停止采集不再关闭 TIM5。这样空闲时
 * PB12 Z 相中断仍然能测速，USART1 才能输出“只旋转、不采集”的 rpm。
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
    TIM_Cmd(TIM5, ENABLE);
}

/** @brief 配置 PB12 下降沿 EXTI，接收低电平有效的 Z 相索引脉冲。 */
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

/**
 * @brief 配置 DMA2 Stream0：ADC_DR -> vendor_adc_dma_buffer 循环搬运。
 *
 * 半字宽度、内存自增、循环模式，并打开半满/满/错误中断。
 * 注意 NVIC 优先级分组必须在写优先级之前配置，见函数内注释。
 */
static void vendor_adc_config_dma(void)
{
    DMA_InitTypeDef dma;

    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_DMA2, ENABLE);
    /*
     * USB 同样使用 PriorityGroup_1。必须先配置分组、再写 DMA 优先级，
     * 这样抢占优先级 0 才会按预期编码。
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
 * 启动顺序为 DMA -> ADC -> TIM5 -> TIM2：保证在第一个 PA5 采样边沿到来之前，
 * 存储通道、ADC 转换和 Z 相时间戳都已经就绪。
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
     * USB 初始化在 ADC 初始化之后执行，会重新设置全局 NVIC 分组。
     * 因此每次启动采集时都重新配置一次 DMA 优先级，
     * 保证 DMA 始终能抢占优先级更低的 USB FS 中断。
     */
    vendor_adc_config_dma_irq();
    NVIC_EnableIRQ(DMA2_Stream0_IRQn);
    DMA_Cmd(DMA2_Stream0, ENABLE);
    ADC_Cmd(ADC1, ENABLE);
    TIM_Cmd(TIM5, ENABLE);
    TIM_Cmd(TIM2, ENABLE);
}

/**
 * @brief 停止采样，并吐出 DMA 中已产生但未上送的采样。
 *
 * 停止 TIM2/ADC/DMA，但保持 TIM5 运行：空闲时仍要用 Z 相测速。
 * 再补发 DMA 中尚未上送的尾部数据。
 */
void vendor_stm32_encoder_adc_stop(void)
{
    uint32_t write_position;
    uint8_t dma_boundary_pending;

    TIM_Cmd(TIM2, DISABLE);
    /*
     * [NEW] TIM5 保持运行：采集停止后仍然需要给 Z 相边沿打时间戳，
     * 这样空闲（只旋转、不采集）时 USART1 也能输出实际 rpm。
     *
     * 同时清掉采集期的 Z 相参考点和旧转速：如果探针中途停过，空闲
     * 测速会重新建立参考，避免拿“很久以前的边沿”算出一个错误的周期。
     * 探针仍在转时，下一个 Z 边沿会很快重新给出实际 rpm。
     */
    vendor_adc_z_reference_valid = 0U;
    vendor_adc_latest_rpm = 0U;
    vendor_adc_speed_updated = 0U;
    ADC_Cmd(ADC1, DISABLE);
    /*
     * 先冻结 DMA 中断。已经完成但尚未处理的采样区间，
     * 会在下面根据 NDTR 和保存的上送游标补发。
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
         * 两个位置相等通常表示尾部为空。
         * 但如果还挂着边界标志，说明 DMA 在上报中断之前已经完整跑完
         * 一整圈 1024 个采样，需要整圈补发。
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
 * [NEW] 控制 PB11 使能引脚。
 *
 * PB11 已在 vendor_adc_config_gpio() 中配置为推挽输出，上电默认高电平。
 * 这里只改变输出电平，不重新配置 GPIO，也不影响 ADC/DMA/USB。
 */
void vendor_stm32_encoder_adc_set_enable_pin(uint8_t high)
{
    if (high != 0U) {
        GPIO_SetBits(GPIOB, GPIO_Pin_11);
    } else {
        GPIO_ResetBits(GPIOB, GPIO_Pin_11);
    }
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
     * 超时判断必须与 Z 相中断互斥。否则中断可能在主循环读取 now 之后
     * 才更新 last_tick，无符号减法会得到一个巨大的超时值，
     * 导致转速短暂误报为 0。
     */
    INTX_DISABLE();

    /* 一个上报周期内没有新的 Z 相边沿，就认为转子已经停转。 */
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

/**
 * @brief 拷贝一份当前采集诊断快照，供 USART 调试遥测使用。
 *
 * 快照在关中断区间内一次性复制，避免前台读到“半更新”的中断统计。
 * 只允许在主循环上下文调用，不要在中断里调用。
 *
 * @param debug 输出参数，保存时间戳、中断计数、ADC 极值、硬件状态等。
 */
void vendor_stm32_encoder_adc_get_debug(vendor_stm32_encoder_adc_debug_t *debug)
{
    uint32_t now;

    if (debug == 0) {
        return;
    }

    /* 该接口只在主循环调用，调用时中断处于开启状态。 */
    INTX_DISABLE();

    now = TIM_GetCounter(TIM5);
    /*
     * [NEW] 停转超时归零：超过 speed_update_ms（默认 500 ms）没有新的
     * Z 相边沿，就把转速当作 0。该判断放在关中断区间内，避免与 Z 相
     * 中断更新 last_tick 竞争；空闲和采集状态都生效。
     *
     * 注意：如果以后把探针转速降到 120 rpm 以下（一转超过 500 ms），
     * 需要同步增大 VENDOR_ADC_DEFAULT_SPEED_MS，否则会被误判为停转。
     */
    if ((vendor_adc_z_reference_valid != 0U) &&
        (vendor_adc_latest_rpm != 0U) &&
        (vendor_adc_speed_timeout_ticks != 0U) &&
        ((uint32_t)(now - vendor_adc_z_last_tick) > vendor_adc_speed_timeout_ticks)) {
        vendor_adc_latest_rpm = 0U;
        vendor_adc_z_reference_valid = 0U;
    }
    debug->timestamp_us = now;
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
     * 如果 USB 中断让本中断等待过久，半满/满两个标志可能同时置位。
     * 此时按环形缓冲区的顺序依次提交；保存的游标可以判断下一个边界是谁。
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
 * TIM2 的 CC2/更新中断当前未使能；ADC 触发完全由硬件链路完成。
 */
void vendor_stm32_encoder_adc_tim_irq_handler(void)
{
    TIM_ClearITPendingBit(TIM2, TIM_IT_Update);
}

/**
 * @brief 保留的 TIM3 中断入口，仅为兼容启动向量表，当前版本不会进入。
 */
void vendor_stm32_encoder_adc_speed_irq_handler(void)
{
}

/**
 * @brief PB12 Z 相中断：用 TIM5 时间戳测量转一圈的时间。
 *
 * 当前原理图中 U8 为低电平有效接收器，因此 EXTI12 配置为下降沿触发。
 * 第一个边沿只建立参考点；之后每个有效边沿都会更新内部 rpm 值。
 */
void vendor_stm32_encoder_adc_z_irq_handler(void)
{
    uint32_t now;
    uint32_t period_ticks;

    if (EXTI_GetITStatus(EXTI_Line12) == RESET) {
        return;
    }

    EXTI_ClearITPendingBit(EXTI_Line12);

    /*
     * [NEW] 空闲时也测速：上位机需要在“只旋转、不采集”时显示 rpm。
     * 采集相关的计数器会在 start() 中统一清零，空闲边沿不会污染采集统计。
     */
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

    /* 每转都重新计算转速，但上报仍遵循原有的 USB 周期（speed_update_ms）。 */
    if ((uint32_t)(now - vendor_adc_z_last_report_tick) >=
        ((uint32_t)vendor_adc_speed_update_ms * (VENDOR_TIM5_TICK_HZ / 1000U))) {
        vendor_adc_z_last_report_tick = now;
        vendor_adc_speed_updated = 1U;
    }
}
