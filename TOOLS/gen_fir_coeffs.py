#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 SERVICE/adc_fir_coeffs.h 的 Q1.15 系数表（CMSIS arm_fir_q15 顺序）。

设计参数（来自 FIR滤波器设计说明书_当前STM32平台.md）：
  fs    = 200000 Hz   (50 r/s x 4000 PPR)
  fc    = 15000 Hz    (-6 dB 点, 归一化 0.075 = 300 次/转)
  taps  = 129         (Blackman-Harris)
  Q1.15 量化, 系数和 = 32768

CMSIS 的 pCoeffs 按时间反序存放: pCoeffs[i] = b[numTaps-1-i]。
为了满足 Cortex-M4 上 arm_fir_init_q15 对偶数抽头的要求:
  b  = [g0 .. g128, 0]        (时域冲激响应末尾补 0, 群延迟仍为 64)
  p  = reverse(b) = [0, g0 .. g128]   (C 数组第 0 个元素是 0)

运行: python TOOLS/gen_fir_coeffs.py
"""

import os
import numpy as np
from scipy import signal

FS = 200000.0
FC = 15000.0
TAPS = 129
Q = 32768
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "SERVICE", "adc_fir_coeffs.h")


def main():
    g = signal.firwin(TAPS, FC, window="blackmanharris", fs=FS)
    q = np.round(g * Q).astype(np.int64)

    # 把舍入误差补到中心抽头，使直流增益严格为 1
    q[TAPS // 2] += Q - int(q.sum())

    assert int(q.sum()) == Q, q.sum()
    assert np.array_equal(q, q[::-1]), "129-tap 系数必须严格对称"

    # 时域冲激响应末尾补 0 -> 130 抽头；CMSIS 顺序是其反序
    b = np.concatenate((q, [0]))
    p = b[::-1]

    # 校验群延迟：冲激峰值应在输入索引 +64。
    # 注意 lfilter 的系数是"时间正序"(b)，CMSIS 的 pCoeffs 是反序(p)，
    # 因此这里用 b 做校验，不能直接用 p。
    imp = np.zeros(400, dtype=np.int64)
    imp[10] = 255
    y = signal.lfilter(b.astype(float) / Q, [1.0], (imp << 7).astype(float))
    peak = int(np.argmax(y)) - 10
    assert peak == 64, peak

    abs_sum = int(np.abs(q).sum())
    assert abs_sum == 52816, abs_sum
    assert int(q.max()) == 4914 and int(q.min()) == -922

    lines = []
    lines.append("/*")
    lines.append(" * [NEW] FIR 系数表（自动生成，请勿手改）")
    lines.append(" *")
    lines.append(" * 生成脚本: TOOLS/gen_fir_coeffs.py")
    lines.append(" * 设计: %d 抽头 Blackman-Harris, fs=%.0f Hz, fc=%.0f Hz" % (TAPS, FS, FC))
    lines.append(" * 量化: Q1.15, 系数和=%d, max=%d, min=%d, |h|和=%d"
                 % (Q, int(q.max()), int(q.min()), abs_sum))
    lines.append(" *")
    lines.append(" * 为什么是 %d 项而不是 %d 项：" % (len(p), TAPS))
    lines.append(" *   CMSIS arm_fir_init_q15 在 Cortex-M4 的 DSP 路径要求偶数抽头，")
    lines.append(" *   129 会被拒绝。这里在时域冲激响应末尾补 1 个 0（等效乘 1），")
    lines.append(" *   传输函数和 64 样本群延迟都不变。")
    lines.append(" *")
    lines.append(" * 为什么第 0 个元素是 0：")
    lines.append(" *   CMSIS 的 pCoeffs 按时间反序存放，reverse([g0..g128, 0]) = [0, g0..g128]。")
    lines.append(" *   若把 0 放在数组末尾，等效群延迟会变成 65 样本，这是一个容易犯的错。")
    lines.append(" */")
    lines.append("#ifndef ADC_FIR_COEFFS_H")
    lines.append("#define ADC_FIR_COEFFS_H")
    lines.append("")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append("#define ADC_FIR_NUM_TAPS  %dU   /* 130 = 129 原系数 + 1 个补零 */" % len(p))
    lines.append("")
    lines.append("/* CMSIS pCoeffs 顺序：[0, g0, g1, ..., g128]；4 字节对齐供 CMSIS 成对读取 */")
    lines.append("static const int16_t adc_fir_coeffs_q15[ADC_FIR_NUM_TAPS]")
    lines.append("    __attribute__((aligned(4))) = {")
    for i in range(0, len(p), 10):
        chunk = ", ".join("%6d" % int(v) for v in p[i:i + 10])
        lines.append("    " + chunk + ("," if i + 10 < len(p) else ""))
    lines.append("};")
    lines.append("")
    lines.append("#endif /* ADC_FIR_COEFFS_H */")
    lines.append("")

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    print("written:", OUT)
    print("sum=%d max=%d min=%d abs_sum=%d group_delay=%d" %
          (int(q.sum()), int(q.max()), int(q.min()), abs_sum, peak))


if __name__ == "__main__":
    main()
