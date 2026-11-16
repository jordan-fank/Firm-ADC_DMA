/*
 * [NEW] ADC 数据 FIR 低通滤波模块
 *
 * 设计依据：FIR滤波器设计说明书_当前STM32平台.md
 *   fs=200 kHz, fc=15 kHz (-6 dB；fc/fs=0.075，相对 Nyquist 归一化 0.15),
 *   129 个设计抽头（Blackman-Harris）+ 1 个补零 = 130 项, Q1.15, 群延迟 64 样本。
 *
 * 与说明书的两处必要修正（详见 adc_fir_coeffs.h 顶部注释）：
 *   1) 抽头数 129 -> 130。
 *      CMSIS arm_fir_init_q15 在 Cortex-M4 的 DSP 路径要求偶数抽头，129 会返回
 *      ARM_MATH_ARGUMENT_ERROR 且不初始化实例。这里在时域冲激响应末尾补 1 个 0，
 *      传输函数和 64 样本群延迟都不变。
 *   2) 状态数组长度 = numTaps + blockSize = 130 + 256 = 386。
 *      说明书写的 numTaps + blockSize - 1 在 M4 上会少 1 个 q15，init 时越界写。
 *
 * 本文件不 include stm32f4xx.h：工程 CORE/core_cm4_simd.h 与 CMSIS 5.x 的
 * cmsis_armclang.h 对 __SMMLA 的定义类型不同，同一个编译单元同时包含会报
 * conflicting types。FIR 模块只依赖 <stdint.h> + CMSIS-DSP 的过滤函数头。
 *
 * 调用位置：只在主循环的 SERVICE 发送路径调用，不在 DMA/USB 中断里调用。
 */

#include "adc_fir.h"

#if ADC_FIR_ENABLE

#include <string.h>
#include "adc_fir_coeffs.h"
#include "dsp/filtering_functions.h"

/* [NEW] 内部块长：状态数组按 256 点分配，实际调用可小于等于 256 点。 */
#define ADC_FIR_BLOCK_SIZE  256U

/* [NEW] M4 上必须是 numTaps + blockSize（不是 -1）。 */
#define ADC_FIR_STATE_SIZE  (ADC_FIR_NUM_TAPS + ADC_FIR_BLOCK_SIZE)

/* [NEW] 4 字节对齐：CMSIS 内部按 32-bit 成对读取 q15 数组。 */
static __attribute__((aligned(4))) q15_t adc_fir_state[ADC_FIR_STATE_SIZE];
static __attribute__((aligned(4))) q15_t adc_fir_in_q15[ADC_FIR_BLOCK_SIZE];
static __attribute__((aligned(4))) q15_t adc_fir_out_q15[ADC_FIR_BLOCK_SIZE];

static arm_fir_instance_q15 adc_fir_instance;
static uint8_t adc_fir_ready;
static uint8_t adc_fir_needs_seed;

/* [NEW] 运行时模式：默认 LOWPASS（上电即滤波），可由 46 00 / 52 00 切换。 */
static adc_fir_mode_t adc_fir_mode = ADC_FIR_MODE_LOWPASS;

/**
 * [NEW] 初始化 FIR 实例（只调用一次）。
 *
 * 系数表 adc_fir_coeffs_q15 已按 CMSIS 的时间反序存放（第 0 个元素是补的 0），
 * 不能直接传正向设计的 g[0..128]。
 */
void adc_fir_init(void)
{
    arm_status status;

    memset(adc_fir_state, 0, sizeof(adc_fir_state));

    status = arm_fir_init_q15(&adc_fir_instance,
                              (uint16_t)ADC_FIR_NUM_TAPS,
                              (const q15_t *)adc_fir_coeffs_q15,
                              adc_fir_state,
                              ADC_FIR_BLOCK_SIZE);

    /* [NEW] 失败时保持 ready=0，process 会直通；正常参数下不会走到这里。 */
    adc_fir_ready = (status == ARM_MATH_SUCCESS) ? 1U : 0U;
    adc_fir_needs_seed = 1U;
}

/**
 * [NEW] 清空历史状态并标记下一次处理需要预热。
 *
 * 只在开始新的采集时调用；停止/暂停时不清，避免在数据流中间制造额外瞬态。
 */
void adc_fir_reset(void)
{
    memset(adc_fir_state, 0, sizeof(adc_fir_state));
    adc_fir_needs_seed = 1U;
}

/**
 * [NEW] 运行时切换滤波/旁路。
 *
 * 只在主循环（命令解析）里调用，不在中断里调用，因此不需要关中断。
 * 从旁路切回低通时清历史，避免把旁路期间的旧状态带进来。
 */
void adc_fir_set_mode(adc_fir_mode_t mode)
{
    if (mode == adc_fir_mode) {
        return;
    }

    adc_fir_mode = mode;

    if (mode == ADC_FIR_MODE_LOWPASS) {
        adc_fir_reset();
    }
}

/** [NEW] 读取当前运行时模式。 */
adc_fir_mode_t adc_fir_get_mode(void)
{
    return adc_fir_mode;
}

/**
 * [NEW] 连续块 FIR。
 *
 * 输入输出都是 8-bit：
 *   q15_in  = (uint16_t)adc_u8 << 7        -> 0..32640，落在 Q15 正半轴
 *   q15_out = arm_fir_q15(...)             -> Q15
 *   out_u8  = saturate((q15_out + 64) >> 7)
 *
 * count 可以不是 256 的倍数：内部按 <=256 分块，arm_fir_q15 允许每次传入
 * 小于初始化块长的点数（M4 实例里没有 blockSize 字段），尾块不会丢样。
 */
void adc_fir_process_block(const uint8_t *in, uint8_t *out, uint16_t count)
{
    uint16_t offset = 0U;
    uint16_t i;

    if ((in == 0) || (out == 0) || (count == 0U)) {
        return;
    }

    /*
     * [NEW] 运行时旁路或初始化失败时直通。
     * 旁路输出与未加滤波时逐字节一致，便于和 46 00 模式做 A/B。
     */
    if ((adc_fir_mode == ADC_FIR_MODE_BYPASS) || (adc_fir_ready == 0U)) {
        memcpy(out, in, count);
        return;
    }

    while (offset < count) {
        uint16_t block = (uint16_t)(count - offset);
        if (block > ADC_FIR_BLOCK_SIZE) {
            block = ADC_FIR_BLOCK_SIZE;
        }

        /*
         * [NEW] 启动预热：用本次采集的第一个样本填满整个状态数组（延迟线）。
         * 这样第一块输出从当前电平开始，而不是从 0 爬升；也不丢弃任何样本，
         * 保证 RAW/FIR 的字节数完全一致。
         *
         * 注意：预热后的前 (ADC_FIR_NUM_TAPS - 1) 个输出仍混有合成历史，
         * 不是被测信号的有效滤波结果。这是为了保持 RAW/FIR 字节数一致而做的
         * 取舍；如果上位机需要严格有效的像素，应对每次 O 之后的这段数据做标记
         * 或在成像时丢弃（设计说明书 §6.2 的要求）。
         */
        if (adc_fir_needs_seed != 0U) {
            q15_t seed = (q15_t)((uint16_t)in[0] << 7);
            for (i = 0U; i < ADC_FIR_STATE_SIZE; i++) {
                adc_fir_state[i] = seed;
            }
            adc_fir_needs_seed = 0U;
        }

        for (i = 0U; i < block; i++) {
            adc_fir_in_q15[i] = (q15_t)((uint16_t)in[offset + i] << 7);
        }

        arm_fir_q15(&adc_fir_instance, adc_fir_in_q15, adc_fir_out_q15, block);

        for (i = 0U; i < block; i++) {
            int32_t value = ((int32_t)adc_fir_out_q15[i] + 64) >> 7;

            if (value < 0) {
                value = 0;
            } else if (value > 255) {
                value = 255;
            }
            out[offset + i] = (uint8_t)value;
        }

        offset = (uint16_t)(offset + block);
    }
}

#endif /* ADC_FIR_ENABLE */
