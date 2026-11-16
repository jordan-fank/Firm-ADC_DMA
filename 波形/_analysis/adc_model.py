# -*- coding: utf-8 -*-
"""折返平均（周期分量 vs 随机分量）+ ADC 采样模型（1.5us 孔径 + 5us 间隔）。
输出：周期分量的峰峰值、剩余随机 RMS、以及换算到 8-bit ADC 的 LSB/条纹幅度。"""
import os, glob
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.dirname(os.path.abspath(__file__))
FS = 500e6
LSB = 3.3 / 256.0           # 12.89 mV
FS_ADC = 200e3              # 3000 rpm × 4000/60
APER = 1.556e-6             # 56 周期 @36MHz

def load(path):
    n = 0
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            n += 1
            if line.strip().startswith('ch1_time'):
                break
    d = np.loadtxt(path, delimiter=',', skiprows=n, encoding='utf-8-sig')
    return d[:, 1], d[:, 3]

def box(x, n):
    n = max(1, int(round(n)))
    c = np.cumsum(np.r_[0.0, x])
    return (c[n:] - c[:-n]) / n

def fold(x, period_samples):
    n = int(round(period_samples))
    m = len(x) // n
    seg = x[:m * n].reshape(m, n)
    return seg.mean(axis=0), seg - seg.mean(axis=0)

def adc_sim(x, fs_ratio=FS / FS_ADC, aper=APER):
    y = box(x, aper * FS)
    nstep = int(round(fs_ratio))
    s = y[::nstep]
    return s

for p in sorted(glob.glob(os.path.join(BASE, '*.csv'))):
    tag = os.path.basename(p).split('-')[0]
    x1, x2 = load(p)
    print('=' * 100)
    print('FILE', tag)
    for ch, x in (('CH1', x1), ('CH2', x2)):
        # 折返：先按 125us(8kHz) 和 62.5us(16kHz)
        for per_us, lab in ((125.0, '8kHz'), (62.5, '16kHz')):
            per = per_us * 1e-6 * FS
            foldw, resid = fold(x, per)
            pp = foldw.max() - foldw.min()
            rms = float(np.sqrt((resid ** 2).mean()))
            if lab == '8kHz':
                print('  %s 周期分量@%s: pk-pk=%.3f V (%.1f LSB)  RMS=%.1f mV | 剩余随机 RMS=%.1f mV (%.1f LSB)'
                      % (ch, lab, pp, pp / LSB, np.std(foldw) * 1e3, rms * 1e3, rms / LSB))
            else:
                print('  %s 周期分量@%s: pk-pk=%.3f V (%.1f LSB)  RMS=%.1f mV'
                      % (ch, lab, pp, pp / LSB, np.std(foldw) * 1e3))

        # ADC 模型
        s = adc_sim(x)
        s = s - s.mean()
        # 固定图案：按 25 点（=8kHz 一拍）折返
        for per_pts, lab in ((25, '8kHz→25列'), (12.5, '16kHz→25列(2相)')):
            n = int(round(per_pts))
            if 25 / n > len(s) // 2:
                continue
            m = len(s) // n
            if m < 10:
                continue
            seg = s[:m * n].reshape(m, n)
            pat = seg.mean(axis=0)
            resid2 = (seg - pat).std()
            print('     ADC模拟(不含真实表面) %s: 固定图案 pk-pk=%.3f V (%.1f LSB), 剩余=%.1f mV (%.1f LSB), 采样数=%d'
                  % (lab, pat.max() - pat.min(), (pat.max() - pat.min()) / LSB,
                     resid2 * 1e3, resid2 / LSB, len(s)))

    # 画折返波形（8kHz）和 ADC 采样点
    fig, ax = plt.subplots(2, 2, figsize=(15, 7.5))
    for j, (ch, x) in enumerate((('CH1', x1), ('CH2', x2))):
        per = 125e-6 * FS
        foldw, resid = fold(x, per)
        tx = np.arange(len(foldw)) * 2e-9 * 1e6
        ax[0, j].plot(tx, foldw * 1e3, color='C%d' % j)
        ax[0, j].set_title('%s folded @8kHz (period 125us)' % ch)
        ax[0, j].set_xlabel('us'); ax[0, j].set_ylabel('mV'); ax[0, j].grid(alpha=0.3)
        s = adc_sim(x); s = s - s.mean()
        n = 25
        m = len(s) // n
        seg = s[:m * n].reshape(m, n)
        pat = seg.mean(axis=0)
        ax[1, j].plot(np.arange(n) * 5, pat * 1e3, 'o-', color='C%d' % j)
        ax[1, j].set_title('%s ADC fixed pattern (25-sample phase, 5us/step)' % ch)
        ax[1, j].set_xlabel('us within 125us frame'); ax[1, j].set_ylabel('mV'); ax[1, j].grid(alpha=0.3)
    fig.suptitle('FILE %s' % tag)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fold_%s.png' % tag), dpi=95)
    plt.close(fig)
print('done')
