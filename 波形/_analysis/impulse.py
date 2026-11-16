# -*- coding: utf-8 -*-
"""脉冲串定位：事件时刻、间隔、与编码器/B相的关系、CH1/CH2 同点比较。"""
import os, glob
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.dirname(os.path.abspath(__file__))
FS = 500e6

def load(path):
    nskip = 0
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            nskip += 1
            if line.strip().startswith('ch1_time'):
                break
    d = np.loadtxt(path, delimiter=',', skiprows=nskip, encoding='utf-8-sig')
    return d[:, 0], d[:, 1], d[:, 2], d[:, 3]

def events(x, k=8.0, min_gap=500):
    a = x - np.median(x)
    mad = np.median(np.abs(a)) * 1.4826
    thr = k * mad
    idx = np.flatnonzero(np.abs(a) > thr)
    if len(idx) == 0:
        return np.array([], dtype=int), thr, mad
    # 合并相邻点为一个事件，取峰位
    groups = np.split(idx, np.flatnonzero(np.diff(idx) > min_gap) + 1)
    peaks = np.array([g[np.argmax(np.abs(a[g]))] for g in groups])
    return peaks, thr, mad

files = sorted(glob.glob(os.path.join(BASE, '*.csv')))
for p in files:
    name = os.path.basename(p)
    tag = name.split('-')[0]
    t1, x1, t2, x2 = load(p)
    print('=' * 100)
    print('FILE', tag, name)
    res = {}
    for ch, x in (('CH1', x1), ('CH2', x2)):
        pk, thr, mad = events(x)
        res[ch] = pk
        dt_us = np.diff(pk) * 2e-6
        print('  %s: MAD=%.1fmV thr=%.0fmV 事件数=%d  间隔(us): 中位=%.1f 均值=%.1f '
              '最小=%.1f 最大=%.1f 标准差=%.1f'
              % (ch, mad * 1e3, thr * 1e3, len(pk),
                 np.median(dt_us), dt_us.mean(), dt_us.min(), dt_us.max(), dt_us.std()))
        # 间隔直方图 TOP5
        if len(dt_us) > 3:
            h, e = np.histogram(dt_us, bins=40)
            top = np.argsort(h)[::-1][:5]
            print('    间隔密集区: ' + ', '.join('%.1f-%.1fus(%d)' % (e[i], e[i+1], h[i]) for i in top))
    # 两通道事件时刻对齐检查
    a, b = res['CH1'], res['CH2']
    if len(a) and len(b):
        # 每个 CH1 事件最近的 CH2 事件距离
        j = np.searchsorted(b, a)
        j = np.clip(j, 1, len(b) - 1)
        d = np.minimum(np.abs(a - b[j]), np.abs(a - b[j - 1])) * 2e-9
        print('  CH1事件到最近CH2事件: 中位=%.1fns, <100ns 的比例=%.1f%%'
              % (np.median(d) * 1e9, 100 * np.mean(d < 100e-9)))

    # 画 20us / 200us 窗口（CH1/CH2 同轴）
    fig, ax = plt.subplots(3, 1, figsize=(15, 9))
    for k, n_us in enumerate((20, 200, 1400)):
        n = int(n_us * 1e-6 * FS)
        ax[k].plot(t1[:n] * 1e6, x1[:n], lw=0.9, color='C0', label='CH1')
        ax[k].plot(t2[:n] * 1e6, x2[:n] - (x2[:n].mean() - x1[:n].mean()), lw=0.6,
                   color='C1', alpha=0.75, label='CH2(平移对齐均值)')
        ax[k].set_xlim(t1[0] * 1e6, t1[min(n, len(t1)) - 1] * 1e6)
        ax[k].set_title('zoom %.0fus' % n_us)
        ax[k].grid(alpha=0.3)
        ax[k].legend(fontsize=8, loc='upper right')
    fig.suptitle('FILE %s' % tag)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'zoom_%s.png' % tag), dpi=95)
    plt.close(fig)
print('done')
