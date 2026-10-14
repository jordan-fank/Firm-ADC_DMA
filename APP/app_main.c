#include "app_main.h"
#include "adc_stream_service.h"
#include "vendor_stm32_encoder_adc.h"
#include "stm32f4xx.h"

#define APP_DEBUG_UART_BAUDRATE       115200U
#define APP_DEBUG_REPORT_PERIOD_US    1000000U
#define APP_DEBUG_LINE_SIZE           320U

typedef struct {
    char data[APP_DEBUG_LINE_SIZE];
    uint16_t length;
} app_debug_line_t;

/* The legacy CMSIS headers in this project do not provide __WFI(). */
static __asm void app_cpu_wait_for_interrupt(void)
{
    WFI
    BX      LR
}

static void app_debug_uart_init(void)
{
    GPIO_InitTypeDef gpio;
    USART_InitTypeDef usart;

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
    usart.USART_BaudRate = APP_DEBUG_UART_BAUDRATE;
    usart.USART_WordLength = USART_WordLength_8b;
    usart.USART_StopBits = USART_StopBits_1;
    usart.USART_Parity = USART_Parity_No;
    usart.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    usart.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
    USART_Init(USART1, &usart);
    USART_Cmd(USART1, ENABLE);
}

static void app_debug_uart_write(const char *data, uint16_t length)
{
    uint16_t i;

    for (i = 0U; i < length; i++) {
        while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET) {
        }
        USART_SendData(USART1, (uint16_t)(uint8_t)data[i]);
    }
}

static void app_debug_line_text(app_debug_line_t *line, const char *text)
{
    while ((*text != '\0') && (line->length < APP_DEBUG_LINE_SIZE)) {
        line->data[line->length++] = *text++;
    }
}

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

static void app_debug_line_field(app_debug_line_t *line,
                                 const char *name,
                                 uint32_t value)
{
    app_debug_line_text(line, " ");
    app_debug_line_text(line, name);
    app_debug_line_text(line, "=");
    app_debug_line_u32(line, value);
}

static void app_debug_uart_event(const char *event)
{
    app_debug_line_t line;

    line.length = 0U;
    app_debug_line_text(&line, "EV ");
    app_debug_line_text(&line, event);
    app_debug_line_text(&line, "\r\n");
    app_debug_uart_write(line.data, line.length);
}

static void app_debug_uart_report(const vendor_stm32_encoder_adc_debug_t *adc,
                                  adc_stream_service_state_t state,
                                  const adc_stream_service_stats_t *service,
                                  uint32_t queue_used)
{
    app_debug_line_t line;
    uint32_t hardware_flags = 0U;

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

static void app_debug_uart_poll(void)
{
    static uint32_t last_report_us;
    static uint8_t previous_active;
    vendor_stm32_encoder_adc_debug_t adc;
    adc_stream_service_stats_t service;

    vendor_stm32_encoder_adc_get_debug(&adc);

    if (adc.acquisition_active != previous_active) {
        previous_active = adc.acquisition_active;
        last_report_us = adc.timestamp_us;
        app_debug_uart_event((adc.acquisition_active != 0U) ? "START" : "STOP");
    }

    if ((adc.acquisition_active == 0U) ||
        ((uint32_t)(adc.timestamp_us - last_report_us) < APP_DEBUG_REPORT_PERIOD_US)) {
        return;
    }

    last_report_us = adc.timestamp_us;
    service = adc_stream_service_get_stats();
    app_debug_uart_report(&adc,
                          adc_stream_service_get_state(),
                          &service,
                          adc_stream_service_get_queue_used());
}

/**
 * @brief 初始化 ADC 数据流服务并持续轮询业务任务。
 *
 * 这是 app 层的业务编排函数：先初始化 service 层，
 * 然后在 while(1) 中不断调用 poll 处理 USB 命令、ADC 发送和转速帧。
 */
void app_main(void)
{
    platform_status_t init_status;
    app_debug_line_t line;

    init_status = adc_stream_service_init();
    app_debug_uart_init();

    line.length = 0U;
    app_debug_line_text(&line, "BOOT uart=115200 service=");
    app_debug_line_u32(&line, (uint32_t)init_status);
    app_debug_line_text(&line, " usb=data pa5=trigger pa2=adc pb12=z idle=wfi\r\n");
    app_debug_uart_write(line.data, line.length);

    while (1) {
        adc_stream_service_poll();
        app_debug_uart_poll();

        /*
         * Cortex-M4 Sleep only: HSE/PLL and USB remain active. A USB OUT
         * interrupt wakes the core, then the next poll consumes O\0 and starts
         * TIM2 -> ADC -> DMA acquisition normally.
         */
        if (adc_stream_service_can_sleep() != 0U) {
            SCB->SCR &= ~SCB_SCR_SLEEPDEEP_Msk;
            __DSB();
            app_cpu_wait_for_interrupt();
        }
    }
}
