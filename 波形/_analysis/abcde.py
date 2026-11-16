# -*- coding: utf-8 -*-
"""A-E 五组排查：140ms/50MSa/s 单通道。折返 8kHz、频谱、事件统计。"""
import os, glob
import numpy as np
import pandas as pd
from scipy import signal
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = r'E:\Desktop\MCU_revision1\排查波形'
OUT = os.path.dirname(os.path.abspath(__file__))
FS = 50e6
LSB_ADC = 3.3 / 256.0

def load(p):
    n = 0
    with open(p, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            n += 1
            if line.strip().startswith('ch2_time'):
                break
    df = pd.read_csv(p, skiprows=n, header=None, names=['t', 'x'],
                     encoding='utf-8-sig', engine='c')
    return df['t'].to_numpy(), df['x'].to_numpy()

def fold(x, per_us=125.0):
    n = int(round(per_us * 1e-6 * FS))
    m = len(x) // n
    seg = x[:m * n].reshape(m, n)
    w = seg.mean(axis=0)
    return w - np.median(w), seg - seg.mean(axis=0)

def best_period(x, lo=123.0, hi=127.0):
    best = (-1, 0)
    for per in np.arange(lo, hi, 0.02):
        w, _ = fold(x, per)
        pp = w.max() - w.min()
        if pp > best[0]:
            best = (pp, per)
    return best

files = sorted(glob.glob(os.path.join(BASE, '*.csv')))
print('=' * 120)
for p in files:
    tag = os.path.basename(p)[0]
    name = os.path.basename(p)
    t, x = load(p)
    x = x.astype(float)
    a = x - np.median(x)
    sig = np.median(np.abs(a)) * 1.4826
    step = np.median(np.diff(np.unique(x))) if len(np.unique(x)) > 2 else 0
    print('\n### %s  (%s)' % (name, tag))
    print('  窗口 %.1f ms | 均值=%.4f V  AC-RMS=%.2f mV  σ(MAD)=%.2f mV  ptp=%.3f V  1-99%%=[%.4f,%.4f]'
          % ((t[-1] - t[0]) * 1e3, x.mean(), x.std() * 1e3, sig * 1e3,
             x.max() - x.min(), np.percentile(x, 1), np.percentile(x, 99)))
    print('  示波器台阶=%.2f mV | 不同值=%d | ADC LSB=12.89mV' % (step * 1e3, len(np.unique(x))))

    pp8, per = best_period(x)
    w8, resid8 = fold(x, per)
    w16, _ = fold(x, 62.5)
    pp16 = w16.max() - w16.min()
    rms_resid = float(np.sqrt((resid8 ** 2).mean()))
    print('  最佳折返周期=%.2f us (%.4f kHz) | 周期分量@8k: %.1f mVpp (%.1f LSB) | @16k: %.1f mVpp'
          % (per, 1e3 / per, pp8 * 1e3, pp8 / LSB_ADC, pp16 * 1e3))
    print('  折返后残余 RMS=%.2f mV (%.2f LSB)' % (rms_resid * 1e3, rms_resid / LSB_ADC))

    # 频段
    w = np.hanning(len(x))
    X = np.fft.rfft(a * w)
    f = np.fft.rfftfreq(len(x), 1 / FS)
    A = np.abs(X) * 2 / w.sum()
    bands = [(0.1, 50), (50, 200), (200, 1e3), (1e3, 10e3), (10e3, 100e3),
             (100e3, 1e6), (1e6, 25e6)]
    txt = ' | '.join('%s:%.2f' % ('%.0f-%.0fHz' % b, np.sqrt(np.sum(A[(f >= b[0]) & (f < b[1])] ** 2) / 2) * 1e3)
                     for b in bands)
    print('  频段RMS(mV): ' + txt)
    # 主峰
    idx = np.argsort(A[1:])[::-1][:6] + 1
    print('  主峰: ' + ', '.join('%.1fHz/%.2fmV' % (f[i], A[i] * 1e3) for i in sorted(idx, key=lambda i: -A[i])))

    # 事件
    thr = 8 * sig
    idxx = np.flatnonzero(np.abs(a) > thr)
    if len(idxx):
        g = np.split(idxx, np.flatnonzero(np.diff(idxx) > 500) + 1)  # 合并 10us 内
        pk = np.array([np.max(np.abs(a[gg])) for gg in g])
        print('  >8σ(%0.fmV) 事件 %d 个, 中位 %.0f mV, p95 %.0f mV, 最大 %.0f mV'
              % (thr * 1e3, len(g), np.median(pk) * 1e3, np.percentile(pk, 95) * 1e3, pk.max() * 1e3))
    else:
        print('  >8σ 无事件')
