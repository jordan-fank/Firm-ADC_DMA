/**
 * @file impl_stm32_debug_uart.c
 * @brief 实现层：把 platform_debug_uart 适配到 USART1（PA9/PA10）。
 *
 * 调试串口只有“初始化”一个动作，且只输出不参与业务数据通路，
 * 因此直接调用 ST 标准外设库完成 GPIO 复用与 USART 配置，未再单独拆 vendor 文件。
 *
 * printf 重定向（fputc）也放在本文件：printf 逐字符调用 fputc，
 * 这里轮询 TXE 后写入 USART1->DR。调试数据量很小，阻塞发送不会明显影响主循环。
 *
 * 文件末尾保留了 printf 重定向前的手写格式化实现，默认不参与编译。
 */

#include "impl_stm32_debug_uart.h"
#include "stm32f4xx.h"
#include <stdio.h>

#define IMPL_DEBUG_UART_BAUDRATE       115200U   /* 调试串口波特率 */

/**
 * @brief 调试串口实现对象。
 *
 * @param base 对外暴露的 platform 调试串口基类对象。
 */
typedef struct {
    platform_debug_uart_t base;
} impl_stm32_debug_uart_t;

static platform_status_t impl_stm32_debug_uart_init(platform_debug_uart_t *uart);

/**
 * @brief 调试串口操作表。
 */
static const platform_debug_uart_ops_t impl_stm32_debug_uart_ops = {
    impl_stm32_debug_uart_init
};

static impl_stm32_debug_uart_t impl_stm32_debug_uart;

/**
 * @brief 只在第一次使用时绑定 base.ops 和 base.impl。
 */
static void impl_stm32_debug_uart_bind(void)
{
    if (impl_stm32_debug_uart.base.ops == 0) {
        impl_stm32_debug_uart.base.ops = &impl_stm32_debug_uart_ops;
        impl_stm32_debug_uart.base.impl = &impl_stm32_debug_uart;
    }
}

/**
 * @brief 初始化 USART1 作为调试串口。
 *
 * PA9 复用为 USART1_TX，PA10 复用为 USART1_RX，参数为 115200-8-N-1，
 * 无硬件流控。该串口只用于输出遥测，不参与业务数据通路。
 *
 * @param uart platform 调试串口对象，本实现不需要使用。
 * @return PLATFORM_OK 表示初始化完成。
 */
static platform_status_t impl_stm32_debug_uart_init(platform_debug_uart_t *uart)
{
    GPIO_InitTypeDef gpio;
    USART_InitTypeDef usart;

    (void)uart;

    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA, ENABLE);
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_USART1, ENABLE);

    gpio.GPIO_Pin = GPIO_Pin_9 | GPIO_Pin_10;
    gpio.GPIO_Mode = GPIO_Mode_AF;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    gpio.GPIO_OType = GPIO_OType_PP;
    gpio.GPIO_PuPd = GPIO_PuPd_UP;
    GPIO_Init(GPIOA, &gpio);
    GPIO_PinAFConfig(GPIOA, GPIO_PinSource9, GPIO_AF_USART1);
    GPIO_PinAFConfig(GPIOA, GPIO_PinSource10, GPIO_AF_USART1);

    USART_StructInit(&usart);
    usart.USART_BaudRate = IMPL_DEBUG_UART_BAUDRATE;
    usart.USART_WordLength = USART_WordLength_8b;
    usart.USART_StopBits = USART_StopBits_1;
    usart.USART_Parity = USART_Parity_No;
    usart.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    usart.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
    USART_Init(USART1, &usart);
    USART_Cmd(USART1, ENABLE);

    return PLATFORM_OK;
}

/**
 * @brief 将标准库 printf 的输出字符重定向到 USART1。
 *
 * printf 最终会逐字符调用 fputc；这里轮询 TXE 标志后写入 USART1->DR。
 * 调试串口数据量很小，阻塞发送不会明显影响主循环。
 *
 * @param ch 待发送字符。
 * @param stream 标准库传入的流对象，本工程不使用。
 * @return 已发送的字符。
 */
int fputc(int ch, FILE *stream)
{
    (void)stream;

    while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET) {
    }

    USART_SendData(USART1, (uint16_t)(uint8_t)ch);
    return ch;
}

/**
 * @brief 获取 STM32 调试串口 platform 对象。
 *
 * @return 指向静态 base 对象的指针。
 */
platform_debug_uart_t *impl_stm32_debug_uart_get(void)
{
    impl_stm32_debug_uart_bind();
    return &impl_stm32_debug_uart.base;
}

/* ============================================================================
 * 以下是 printf 重定向前的手写格式化实现，仅作参考保留（默认不编译）。
 *
 * USER_FORMAT = 0 时整段被预处理掉，不影响固件；
 * 改成 1 时才会参与编译，且报告函数需要引用 SERVICE/VENDOR 的类型，
 * 因此相关头文件也放在条件编译内部。
 * ========================================================================== */

#define USER_FORMAT 0        /* 是否启用自定义格式，1 打开，0 关闭 */

#if USER_FORMAT

#include "adc_stream_service.h"
#include "vendor_stm32_encoder_adc.h"

#define APP_DEBUG_LINE_SIZE           320U       /* 单行调试文本的最大长度 */

/**
 * @brief 调试输出使用的定长文本行。
 *
 * 不使用 printf/sprintf，避免引入标准库格式化代码增大 Flash 占用；
 * 所有数字均由 app_debug_line_u32() 手工转成十进制字符。
 *
 * @param data 字符缓冲区。
 * @param length 当前已经写入的字符数。
 */
typedef struct {
    char data[APP_DEBUG_LINE_SIZE];
    uint16_t length;
} app_debug_line_t;

/**
 * @brief 轮询方式向 USART1 发送一段文本。
 *
 * 每次写数据前等待 TXE（发送数据寄存器空）标志，属于阻塞发送；
 * 调试串口数据量很小，不会明显影响主循环。
 *
 * @param data 待发送文本，不要求以 '\0' 结尾。
 * @param length 要发送的字符数。
 */
static void app_debug_uart_write(const char *data, uint16_t length)
{
    uint16_t i;

    for (i = 0U; i < length; i++) {
        while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET) {
        }
        USART_SendData(USART1, (uint16_t)(uint8_t)data[i]);
    }
}

/**
 * @brief 向调试行追加一个字符串。
 *
 * 超出 APP_DEBUG_LINE_SIZE 的部分会被截断，保证不会越界。
 *
 * @param line 目标调试行。
 * @param text 以 '\0' 结尾的字符串。
 */
static void app_debug_line_text(app_debug_line_t *line, const char *text)
{
    while ((*text != '\0') && (line->length < APP_DEBUG_LINE_SIZE)) {
        line->data[line->length++] = *text++;
    }
}

/**
 * @brief 向调试行追加一个无符号十进制整数。
 *
 * 先把数字逆序写入局部数组，再正向拷入目标行，避免使用 sprintf。
 *
 * @param line 目标调试行。
 * @param value 要输出的无符号整数。
 */
static void app_debug_line_u32(app_debug_line_t *line, uint32_t value)
{
    char reversed[10];
    uint8_t count = 0U;

    do {
        reversed[count++] = (char)('0' + (value % 10U));
        value /= 10U;
    } while ((value != 0U) && (count < sizeof(reversed)));

    while ((count > 0U) && (line->length < APP_DEBUG_LINE_SIZE)) {
        line->data[line->length++] = reversed[--count];
    }
}

/**
 * @brief 向调试行追加一个 " 名称=数值" 字段。
 *
 * 例如 name="rpm"、value=1500 时追加 " rpm=1500"。
 *
 * @param line 目标调试行。
 * @param name 字段名。
 * @param value 字段数值。
 */
static void app_debug_line_field(app_debug_line_t *line,
                                 const char *name,
                                 uint32_t value)
{
    app_debug_line_text(line, " ");
    app_debug_line_text(line, name);
    app_debug_line_text(line, "=");
    app_debug_line_u32(line, value);
}

/**
 * @brief 输出一行采集状态变化事件。
 *
 * @param event 事件名，例如 "START" / "STOP"。
 */
static void app_debug_uart_event(const char *event)
{
    app_debug_line_t line;

    line.length = 0U;
    app_debug_line_text(&line, "EV ");
    app_debug_line_text(&line, event);
    app_debug_line_text(&line, "\r\n");
    app_debug_uart_write(line.data, line.length);
}

/**
 * @brief 输出一行完整的运行指标（"D ..." 调试遥测）。
 *
 * 字段含义（与 README「调试输出」章节一致）：
 *   t  = TIM5 时间戳，单位 us
 *   st = service 状态（0 未初始化 / 1 停止 / 2 采集中 / 3 暂停 / 4 错误）
 *   rpm= 当前转速
 *   h/f= DMA 半满 / 满中断累计次数
 *   n  = 已上送的采样字节总数
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
    app_debug_line_t line;
    uint32_t hardware_flags = 0U;

    /* hw 位定义：bit0 采集运行 bit1 ADC 使能 bit2 DMA 使能 bit3 TIM2 使能
     *            bit4 PA5(B 相)电平 bit5 已收到 ADC 采样 */
    hardware_flags |= (adc->acquisition_active != 0U) ? 1U : 0U;
    hardware_flags |= (adc->adc_enabled != 0U) ? 2U : 0U;
    hardware_flags |= (adc->dma_enabled != 0U) ? 4U : 0U;
    hardware_flags |= (adc->tim2_enabled != 0U) ? 8U : 0U;
    hardware_flags |= (adc->phase_b_level != 0U) ? 16U : 0U;
    hardware_flags |= (adc->adc_sample_seen != 0U) ? 32U : 0U;

    line.length = 0U;
    app_debug_line_text(&line, "D");
    app_debug_line_field(&line, "t", adc->timestamp_us);
    app_debug_line_field(&line, "st", (uint32_t)state);
    app_debug_line_field(&line, "rpm", adc->rpm);
    app_debug_line_field(&line, "h", adc->dma_half_irq_count);
    app_debug_line_field(&line, "f", adc->dma_full_irq_count);
    app_debug_line_field(&line, "n", adc->samples_emitted);
    app_debug_line_field(&line, "nd", adc->dma_remaining);
    app_debug_line_field(&line, "ci", adc->tim2_capture_seen);
    app_debug_line_field(&line, "co", adc->tim2_capture_overflow);
    app_debug_line_field(&line, "c1", adc->tim2_capture1);
    app_debug_line_field(&line, "q", queue_used);
    app_debug_line_field(&line, "pk", service->adc_queue_peak);
    app_debug_line_field(&line, "ub", service->usb_busy_returns);
    app_debug_line_field(&line, "ue", service->usb_busy_episodes);
    app_debug_line_field(&line, "p", service->adc_pause_count);
    app_debug_line_field(&line, "rs", service->adc_resume_count);
    app_debug_line_field(&line, "dr", service->samples_dropped);
    app_debug_line_field(&line, "mn", adc->adc_min);
    app_debug_line_field(&line, "mx", adc->adc_max);
    app_debug_line_field(&line, "ls", adc->adc_last);
    app_debug_line_field(&line, "ov", adc->adc_overrun);
    app_debug_line_field(&line, "te", adc->dma_transfer_error_count);
    app_debug_line_field(&line, "de", adc->dma_direct_error_count);
    app_debug_line_field(&line, "fe", adc->dma_fifo_error_count);
    app_debug_line_field(&line, "z", adc->z_irq_count);
    app_debug_line_field(&line, "zp", adc->last_z_period_us);
    app_debug_line_field(&line, "hw", hardware_flags);
    app_debug_line_text(&line, "\r\n");
    app_debug_uart_write(line.data, line.length);
}

/**
 * @brief 原手写格式化版本的启动横幅（保留备查）。
 *
 * @param init_status service 初始化返回值。
 */
static void app_debug_uart_boot_line(uint32_t init_status)
{
    app_debug_line_t line;

    line.length = 0U;
    app_debug_line_text(&line, "BOOT uart=115200 service=");
    app_debug_line_u32(&line, init_status);
    app_debug_line_text(&line, " usb=data pa5=trigger pa2=adc pb12=z idle=wfi\r\n");
    app_debug_uart_write(line.data, line.length);
}

#endif /* USER_FORMAT */
