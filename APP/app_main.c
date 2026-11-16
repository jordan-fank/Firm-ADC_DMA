/**
 * @file app_main.c
 * @brief 应用层：系统启动、调试遥测与主循环调度。
 *
 * 分层位置：USER/main.c -> APP(本文件) -> SERVICE -> PLATFORM -> IMPLEMENT -> VENDOR。
 * 本文件只做两件事：
 *   1. 初始化 SERVICE 层业务服务，并在 while(1) 中持续调用 adc_stream_service_poll()；
 *   2. 输出调试遥测，方便脱离上位机排查问题。
 *
 * 主数据流：
 *   编码器 PA5 -> TIM2 触发 -> ADC1 -> DMA -> SERVICE 环形队列 -> USB CDC -> 上位机。
 *
 * 调试输出：
 *   - 统一使用标准库 printf；fputc 由 IMPLEMENT 层重定向到 USART1，
 *     本文件只通过 platform_board 的 debug_uart 对象完成初始化，不接触任何寄存器；
 *   - 采集启动/停止时输出一行 "EV START" / "EV STOP"；
 *   - 采集中每秒输出一行 "D t=... st=... rpm=... ..."，字段含义见 app_debug_uart_report()；
 *   - 空闲时每 500 ms 输出一行 "RPM rpm=... st=... z=... zp=..."，
 *     用于上位机在“只旋转、不采集”时显示探针实际转速。
 *
 * 说明：原手写十进制格式化代码已移到 IMPLEMENT/impl_stm32_debug_uart.c 末尾，
 * 仍由 USER_FORMAT 宏控制（默认 0，不参与编译）。
 */

#include "app_main.h"
#include "adc_stream_service.h"
#include "platform_board.h"
#include "vendor_stm32_encoder_adc.h"
#include <stdio.h>

#define APP_DEBUG_REPORT_PERIOD_US    1000000U   /* 采集期 D 行上报周期：1 秒 */
#define APP_IDLE_REPORT_PERIOD_US     500000U    /* 空闲期 RPM 短行上报周期：500 ms */

/**
 * @brief 让 Cortex-M4 内核进入 Sleep 等待中断。
 *
 * 工程内旧版 CMSIS 头文件没有提供 __WFI() 内联函数，因此直接用内联汇编
 * 执行 WFI，再用 bx lr 返回。naked 属性保证编译器不会插入额外的
 * 栈保护/返回指令，避免破坏手写汇编的返回流程。
 *
 * 注意：当前主循环中的调用点已被注释掉（见 app_main()），该函数仅作预留。
 */
static __attribute__((naked)) void app_cpu_wait_for_interrupt(void)
{
    __asm volatile("wfi\n\tbx lr\n");
}

/**
 * @brief 输出一行采集状态变化事件。
 *
 * @param event 事件名，例如 "START" / "STOP"。
 */
static void app_debug_uart_event(const char *event)
{
    printf("EV %s\r\n", event);
}

/**
 * @brief 输出一行完整的运行指标（"D ..." 调试遥测）。
 *
 * 字段含义（与 README「调试输出」章节一致）：
 *   t  = TIM5 时间戳，单位 us
 *   st = service 状态（0 未初始化 / 1 停止 / 2 采集中 / 3 暂停 / 4 错误）
 *   rpm= 当前转速
 *   h/f= DMA 半满 / 满中断累计次数
 *   n  = 已产出的采样字节总数（DMA 提交给 service 的数量）
 *   sn = 已成功写入 USB 发送环的 ADC 字节数（service 统计）
 *   nd = DMA 剩余计数（NDTR）
 *   ci/co/c1 = TIM2 捕获状态 / 捕获溢出 / CCR1 捕获值
 *   q/pk= 当前 / 峰值 ADC 队列水位
 *   ub/ue = USB 忙返回次数 / 连续忙段数
 *   p/rs= 因背压暂停 / 恢复采样次数
 *   dr = 丢弃的采样字节数
 *   mn/mx/ls = ADC 最小值 / 最大值 / 最新值
 *   ov = ADC 溢出标志
 *   te/de/fe = DMA 传输 / 直接模式 / FIFO 错误计数
 *   z/zp= Z 相中断次数 / 最近一次周期（us）
 *   hw = 硬件状态位掩码（见下方 hardware_flags 注释）
 *
 * @param adc vendor 层采集诊断快照。
 * @param state service 当前状态。
 * @param service service 层统计信息快照。
 * @param queue_used 当前 ADC 服务队列中的字节数。
 */
static void app_debug_uart_report(const vendor_stm32_encoder_adc_debug_t *adc,
                                  adc_stream_service_state_t state,
                                  const adc_stream_service_stats_t *service,
                                  uint32_t queue_used)
{
    uint32_t hardware_flags = 0U;

    /* hw 位定义：bit0 采集运行 bit1 ADC 使能 bit2 DMA 使能 bit3 TIM2 使能
     *            bit4 PA5(B 相)电平 bit5 已收到 ADC 采样 */
    hardware_flags |= (adc->acquisition_active != 0U) ? 1U : 0U;
    hardware_flags |= (adc->adc_enabled != 0U) ? 2U : 0U;
    hardware_flags |= (adc->dma_enabled != 0U) ? 4U : 0U;
    hardware_flags |= (adc->tim2_enabled != 0U) ? 8U : 0U;
    hardware_flags |= (adc->phase_b_level != 0U) ? 16U : 0U;
    hardware_flags |= (adc->adc_sample_seen != 0U) ? 32U : 0U;

    printf("D t=%u st=%u rpm=%u h=%u f=%u n=%u sn=%u nd=%u ci=%u co=%u c1=%u "
           "q=%u pk=%u ub=%u ue=%u p=%u rs=%u dr=%u mn=%u mx=%u ls=%u "
           "ov=%u te=%u de=%u fe=%u z=%u zp=%u hw=%u\r\n",
           (unsigned)adc->timestamp_us,
           (unsigned)state,
           (unsigned)adc->rpm,
           (unsigned)adc->dma_half_irq_count,
           (unsigned)adc->dma_full_irq_count,
           (unsigned)adc->samples_emitted,
           (unsigned)service->samples_sent,
           (unsigned)adc->dma_remaining,
           (unsigned)adc->tim2_capture_seen,
           (unsigned)adc->tim2_capture_overflow,
           (unsigned)adc->tim2_capture1,
           (unsigned)queue_used,
           (unsigned)service->adc_queue_peak,
           (unsigned)service->usb_busy_returns,
           (unsigned)service->usb_busy_episodes,
           (unsigned)service->adc_pause_count,
           (unsigned)service->adc_resume_count,
           (unsigned)service->samples_dropped,
           (unsigned)adc->adc_min,
           (unsigned)adc->adc_max,
           (unsigned)adc->adc_last,
           (unsigned)adc->adc_overrun,
           (unsigned)adc->dma_transfer_error_count,
           (unsigned)adc->dma_direct_error_count,
           (unsigned)adc->dma_fifo_error_count,
           (unsigned)adc->z_irq_count,
           (unsigned)adc->last_z_period_us,
           (unsigned)hardware_flags);
}

/**
 * @brief 调试遥测的周期调度入口。
 *
 * 每次主循环调用一次：
 *   - 检测到采集状态翻转时立即输出 EV START / EV STOP；
 *   - 采集中每 APP_DEBUG_REPORT_PERIOD_US（1 秒）输出一行 "D ..." 指标；
 *   - 空闲时每 APP_IDLE_REPORT_PERIOD_US（500 ms）输出一行短的
 *     "RPM rpm=... st=... z=... zp=..."，用于“只旋转、不采集”时显示转速。
 *
 * 采集期 D 行的字段、频率完全不变；空闲行只在 ADC 未采集时输出，
 * 不会进入 USB 数据流，也不会影响采集与生图。
 */
static void app_debug_uart_poll(void)
{
    static uint32_t last_report_us;
    static uint32_t last_idle_report_us;
    static uint8_t previous_active;
    vendor_stm32_encoder_adc_debug_t adc;
    adc_stream_service_stats_t service;

    vendor_stm32_encoder_adc_get_debug(&adc);

    if (adc.acquisition_active != previous_active) {
        previous_active = adc.acquisition_active;
        last_report_us = adc.timestamp_us;
        last_idle_report_us = adc.timestamp_us;
        app_debug_uart_event((adc.acquisition_active != 0U) ? "START" : "STOP");
    }

    if (adc.acquisition_active != 0U) {
        if ((uint32_t)(adc.timestamp_us - last_report_us) < APP_DEBUG_REPORT_PERIOD_US) {
            return;
        }

        last_report_us = adc.timestamp_us;
        service = adc_stream_service_get_stats();
        app_debug_uart_report(&adc,
                              adc_stream_service_get_state(),
                              &service,
                              adc_stream_service_get_queue_used());
        return;
    }

    /*
     * [NEW] 空闲（只旋转、不采集）：短转速行。ADC/DMA/USB 均未采集，
     * 这一行的 printf 阻塞不会影响采样；采集期间仍只输出原来的 D 行。
     */
    if ((uint32_t)(adc.timestamp_us - last_idle_report_us) < APP_IDLE_REPORT_PERIOD_US) {
        return;
    }

    last_idle_report_us = adc.timestamp_us;
    printf("RPM rpm=%u st=%u z=%u zp=%u\r\n",
           (unsigned)adc.rpm,
           (unsigned)adc_stream_service_get_state(),
           (unsigned)adc.z_irq_count,
           (unsigned)adc.last_z_period_us);
}

/**
 * @brief 初始化 ADC 数据流服务并持续轮询业务任务。
 *
 * 这是 app 层的业务编排函数：先初始化 service 层与调试串口，
 * 然后在 while(1) 中不断调用 poll 处理 USB 命令、ADC 发送和转速帧。
 */
void app_main(void)
{
    const platform_board_t *board;
    platform_status_t init_status;

    init_status = adc_stream_service_init();

    /* 调试串口只通过 platform 抽象初始化，APP 不接触 USART/GPIO 寄存器。 */
    board = platform_board_get();
    if ((board != 0) && (board->debug_uart != 0)) {
        (void)board->debug_uart->ops->init(board->debug_uart);
    }

    printf("BOOT uart=115200 service=%u usb=data pa5=trigger pa2=adc pb12=z idle=wfi\r\n",
           (unsigned)init_status);

    while (1) {
        adc_stream_service_poll();
        app_debug_uart_poll();

        /*
         * Cortex-M4 仅进入 Sleep 模式：HSE/PLL 和 USB 保持运行。
         * USB OUT 中断会唤醒内核，下一次 poll 收到 O\0 后
         * 正常启动 TIM2 -> ADC -> DMA 采集。
         *
         * 目前整段低功耗路径被注释掉，原因是 USB 唤醒调试期间先保持
         * 主循环全速轮询；需要低功耗时再打开。
         */
//        if (adc_stream_service_can_sleep() != 0U) {
//            SCB->SCR &= ~SCB_SCR_SLEEPDEEP_Msk;
//            __DSB();
//            app_cpu_wait_for_interrupt();
//        }
    }
}
