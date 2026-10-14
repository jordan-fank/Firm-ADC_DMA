#include "platform_board.h"
#include "impl_stm32_encoder_adc.h"
#include "impl_usb_cdc_device.h"
#include "impl_usb_cdc_stream.h"

static platform_board_t impl_stm32_board;

/**
 * @brief 绑定并返回当前 STM32 板级 platform 对象集合。
 *
 * 这是 service 层进入 implement 层的唯一板级入口：
 * 把 ADC 采样流、USB 字节流和 USB 设备对象填入 platform_board_t。
 *
 * @return 指向静态板级对象集合的只读指针。
 */
const platform_board_t *platform_board_get(void)
{
    impl_stm32_board.adc_stream = impl_stm32_encoder_adc_get_stream();
    impl_stm32_board.byte_stream = impl_usb_cdc_stream_get();
    impl_stm32_board.usb_device = impl_usb_cdc_device_get();

    return &impl_stm32_board;
}
