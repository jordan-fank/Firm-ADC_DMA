/**
 * @file impl_stm32_board.c
 * @brief 实现层：板级对象组装入口。
 *
 * 把当前 STM32 板卡上的四个 platform 对象（ADC 采样流、USB 字节流、
 * USB 设备、调试串口）组装成 platform_board_t，供上层统一获取。
 * 更换板卡时，只需要修改本文件和对应的 impl_* 实现。
 */

#include "platform_board.h"
#include "impl_stm32_debug_uart.h"
#include "impl_stm32_encoder_adc.h"
#include "impl_usb_cdc_device.h"
#include "impl_usb_cdc_stream.h"
#include "vendor_stm32_encoder_adc.h"   /* [NEW] PB11 使能引脚控制 */

static platform_board_t impl_stm32_board;

/**
 * @brief 绑定并返回当前 STM32 板级 platform 对象集合。
 *
 * 这是上层进入 implement 层的唯一板级入口：
 * 把 ADC 采样流、USB 字节流、USB 设备和调试串口对象填入 platform_board_t。
 *
 * @return 指向静态板级对象集合的只读指针。
 */
const platform_board_t *platform_board_get(void)
{
    impl_stm32_board.adc_stream = impl_stm32_encoder_adc_get_stream();
    impl_stm32_board.byte_stream = impl_usb_cdc_stream_get();
    impl_stm32_board.usb_device = impl_usb_cdc_device_get();
    impl_stm32_board.debug_uart = impl_stm32_debug_uart_get();

    return &impl_stm32_board;
}

/**
 * [NEW] 板级使能引脚控制：把 platform 层请求转发到 vendor 层。
 *
 * @param high 非 0：PB11 输出高（使能）；0：输出低（失能）。
 * @return PLATFORM_OK 表示已经写入 GPIO。
 */
platform_status_t platform_board_set_enable(uint8_t high)
{
    vendor_stm32_encoder_adc_set_enable_pin(high);
    return PLATFORM_OK;
}
