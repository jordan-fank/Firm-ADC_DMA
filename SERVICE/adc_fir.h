#ifndef ADC_FIR_H
#define ADC_FIR_H

/*
 * [NEW] ADC 数据 FIR 低通滤波模块（独立文件，不依赖 PLATFORM/IMPLEMENT/VENDOR）
 *
 * 一键开关：
 *   ADC_FIR_ENABLE = 1  启用滤波（默认，烧录后上电即生效）
 *   ADC_FIR_ENABLE = 0  关闭滤波，完全走原有路径；关闭时的代码路径与改动前一致，
 *                       便于用同一套硬件做 RAW / FIR 对比。
 *
 * 也可以在编译命令里覆盖，例如：-DADC_FIR_ENABLE=0
 */

#include <stdint.h>

#ifndef ADC_FIR_ENABLE
#define ADC_FIR_ENABLE 0

#endif

#if ADC_FIR_ENABLE

/**
 * [NEW] 运行时滤波模式。
 *
 * 默认上电为 LOWPASS（与之前"上电即滤波"的行为一致）；
 * 串口命令可以随时切换：
 *   46 00 ('F' + 0x00) -> LOWPASS
 *   52 00 ('R' + 0x00) -> BYPASS
 * O/C（4F 00 / 43 00）的语义不变。
 */
typedef enum {

    ADC_FIR_MODE_BYPASS = 0,
    ADC_FIR_MODE_LOWPASS = 1
} adc_fir_mode_t;

/**
 * [NEW] 初始化 FIR 实例。只需要在系统初始化时调用一次。
 * 内部会检查 arm_fir_init_q15 的返回值；失败时 process 会自动退化为直通，
 * 保证采集链路仍然可用（但正常参数下不会失败）。
 */
void adc_fir_init(void);

/**
 * [NEW] 清空 FIR 历史状态。每次开始新的采集（收到 O\0）时调用，
 * 下一次处理会用第一个输入样本预热延迟线，避免输出从 0 爬升。
 */
void adc_fir_reset(void);

/**
 * [NEW] 运行时切换滤波/旁路。
 *
 * 切到 LOWPASS 时会清空历史状态，下一次处理用首个样本重新预热；
 * 切到 BYPASS 时原样输出，数据与未加滤波时完全一致。
 * 由 SERVICE 层解析 46 00 / 52 00 命令后调用。
 */
void adc_fir_set_mode(adc_fir_mode_t mode);

/** [NEW] 读取当前运行时模式。 */
adc_fir_mode_t adc_fir_get_mode(void);

/**
 * [NEW] 对一段连续的 8-bit ADC 数据做滤波。
 *
 * @param in    输入原始采样（0..255）
 * @param out   输出滤波后的 8-bit 采样（0..255），不能与 in 是同一缓冲区
 * @param count 本次样本数；任意长度均可，内部按最多 256 点分块，
 *              FIR 状态在块与块之间保持连续
 *
 * 缩放：输入 << 7 转 Q15，输出 (y + 64) >> 7 并饱和到 0..255。
 */
void adc_fir_process_block(const uint8_t *in, uint8_t *out, uint16_t count);

#endif /* ADC_FIR_ENABLE */

#endif /* ADC_FIR_H */
