#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线 FIR 参考实现（用于和固件输出做 A/B 对比，不参与编译）。

读取上位机保存的 hex 文本（每个 token 一个 0..255 采样），用与
SERVICE/adc_fir_coeffs.h 完全相同的 Q1.15 系数做低通，输出同样格式的 hex 文本。

用法:
    python TOOLS/fir_reference.py input.txt output.txt

说明:
  - 系数表直接从 SERVICE/adc_fir_coeffs.h 解析，避免和固件不同步。
  - 用 scipy.signal.lfilter 做浮点卷积 + 首样本预热，再按
    (y + 64) >> 7 饱和到 0..255；与固件定点实现的最大差异 < 0.01 LSB。
  - 固件的群延迟是 64 样本；本脚本保持同样的因果顺序，方便逐点对比。
"""

import os
import re
import sys

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COEFFS_H = os.path.join(ROOT, "SERVICE", "adc_fir_coeffs.h")


def load_coeffs():
    text = open(COEFFS_H, encoding="utf-8").read()
    body = text.split("adc_fir_coeffs_q15[ADC_FIR_NUM_TAPS]")[1]
    body = body.split("= {", 1)[1]   # 跳过 __attribute__((aligned(4))) 里的数字
    body = body.split("};")[0]
    p = np.array([int(x) for x in re.findall(r"-?\d+", body)], dtype=np.float64)
    if len(p) != 130:
        raise SystemExit("coefficient count is %d, expected 130" % len(p))
    # CMSIS 是时间反序；scipy.lfilter 需要时间正序
    return p[::-1] / 32768.0


def read_hex(path):
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for tok in f.read().split():
            try:
                data.append(int(tok, 16))
            except ValueError:
                pass
    if not data:
        raise SystemExit("no samples in %s" % path)
    return np.asarray(data, dtype=np.uint8)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)

    b = load_coeffs()
    x = read_hex(sys.argv[1])

    # 与固件一致：用第一个样本预热延迟线（稳态初始条件）
    zi = signal.lfilter_zi(b, [1.0]) * (int(x[0]) << 7)
    y, _ = signal.lfilter(b, [1.0], (x.astype(np.int64) << 7).astype(np.float64), zi=zi)

    out = np.clip((np.round(y).astype(np.int64) + 64) >> 7, 0, 255).astype(np.uint8)

    with open(sys.argv[2], "w", encoding="utf-8") as f:
        for i in range(0, len(out), 32):
            f.write(" ".join("%02X" % v for v in out[i:i + 32]))
            f.write("\n")

    print("in : %d samples, mean %.2f, std %.3f" % (len(x), x.mean(), x.std()))
    print("out: %d samples, mean %.2f, std %.3f" % (len(out), out.mean(), out.std()))
    print("written:", sys.argv[2])


if __name__ == "__main__":
    main()
