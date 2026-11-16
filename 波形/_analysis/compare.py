# -*- coding: utf-8 -*-
"""跨文件稳健对比：基线噪声、瞬态事件率/幅度、0-100kHz 带内 RMS、编码器频带。"""
import os, glob
import numpy as np
from scipy import signal

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FS = 500e6
LSB = 3.3 / 256.0

def load(path):
    n = 0
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            n += 1
            if line.strip().startswith('ch1_time'):
                break
    d = np.loadtxt(path, delimiter=',', skiprows=n, encoding='utf-8-sig')
    return d[:, 1], d[:, 3]

def bandrms(x, lo, hi, order=4):
    sos = signal.butter(order, [lo / (FS / 2), hi / (FS / 2)], 'bandpass', output='sos')
    y = signal.sosfiltfilt(sos, x - x.mean())
    return float(np.sqrt((y ** 2).mean()))

print('%-4s %-4s %8s %8s %9s %9s %9s %9s %9s %9s %9s' % (
    'file', 'ch', 'baseline', 'thr', 'ev/ms', 'ev_pk50', 'ev_pk95', 'ev_max',
    '>5LSB%', 'RMS0-100k', 'RMS190-210k'))
print('%-4s %-4s %8s %8s %9s %9s %9s %9s %9s %9s %9s' % (
    '', '', 'mV', 'mV', '', 'V', 'V', 'V', '%', 'mV', 'mV'))
rows = []
for p in sorted(glob.glob(os.path.join(BASE, '*.csv'))):
    tag = os.path.basename(p).split('-')[0]
    x1, x2 = load(p)
    for ch, x in (('CH1', x1), ('CH2', x2)):
        med = np.median(x)
        a = x - med
        sig = np.median(np.abs(a)) * 1.4826
        thr = 6 * sig
        # 事件：超过 thr，合并 1us 内
        idx = np.flatnonzero(np.abs(a) > thr)
        if len(idx):
            g = np.split(idx, np.flatnonzero(np.diff(idx) > 500) + 1)
            pk = np.array([np.max(np.abs(a[gg])) for gg in g])
        else:
            pk = np.array([])
        frac = float(np.mean(np.abs(a) > 5 * LSB))
        b1 = bandrms(x, 1e3, 100e3)
        b2 = bandrms(x, 190e3, 210e3)
        rate = len(pk) / (len(x) / FS * 1e3) if len(pk) else 0.0
        rows.append((tag, ch, sig, thr, rate, pk, frac, b1, b2))
        print('%-4s %-4s %8.1f %8.0f %9.1f %9.3f %9.3f %9.3f %9.2f %9.1f %9.1f' % (
            tag, ch, sig * 1e3, thr * 1e3, rate,
            np.median(pk) if len(pk) else 0, np.percentile(pk, 95) if len(pk) else 0,
            pk.max() if len(pk) else 0, 100 * frac, b1 * 1e3, b2 * 1e3))
