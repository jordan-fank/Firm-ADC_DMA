# -*- coding: utf-8 -*-
"""频谱 / 自相关 / 突发 / 相关结构分析。只读 CSV。"""
import os, glob
import numpy as np
from scipy import signal
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.dirname(os.path.abspath(__file__))
FS = 500e6

def load(path):
    meta, nskip = {}, 0
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            nskip += 1
            s = line.strip()
            if s.startswith('ch1_time'):
                break
            if ':' in s:
                k, v = s.split(':', 1)
                meta.setdefault(k.strip(), []).append(v.strip())
    d = np.loadtxt(path, delimiter=',', skiprows=nskip, encoding='utf-8-sig')
    return meta, d[:, 0], d[:, 1], d[:, 2], d[:, 3]

def spec(x):
    x = x - x.mean()
    w = np.hanning(len(x))
    X = np.fft.rfft(x * w)
    f = np.fft.rfftfreq(len(x), 1 / FS)
    amp = np.abs(X) * 2 / w.sum()          # 单边幅度（正弦峰值）
    return f, amp

def peaks(f, amp, fmin, fmax, n=8, minsep_bins=2):
    m = (f >= fmin) & (f <= fmax)
    ff, aa = f[m], amp[m]
    idx = signal.find_peaks(aa, distance=minsep_bins)[0]
    idx = idx[np.argsort(aa[idx])[::-1][:n]]
    return sorted([(ff[i], aa[i]) for i in idx], key=lambda z: -z[1])

def burst_stats(x, k=6.0):
    a = x - np.median(x)
    mad = np.median(np.abs(a)) * 1.4826
    if mad <= 0:
        return None
    thr = k * mad
    mask = np.abs(a) > thr
    if not mask.any():
        return dict(n=0, thr=thr, mad=mad)
    edges = np.flatnonzero(np.diff(mask.astype(np.int8)))
    starts = np.r_[0, edges + 1][mask[np.r_[0, edges + 1]]]
    ends = np.r_[edges, len(mask) - 1][mask[np.r_[edges, len(mask) - 1]]]
    widths = (ends - starts + 1)
    amps = [np.max(np.abs(a[s:e + 1])) for s, e in zip(starts, ends)]
    ia = np.diff(starts) * 2e-9 if len(starts) > 1 else np.array([])
    return dict(n=int(mask.sum()), n_events=int(len(starts)), thr=thr, mad=mad,
                width_us_max=float(widths.max() * 2e-6),
                width_us_med=float(np.median(widths) * 2e-6),
                amp_max=float(np.max(amps)),
                duty=float(mask.mean()),
                ia_n=len(ia), ia_med_us=float(np.median(ia) * 1e6) if len(ia) else 0.0)

files = sorted(glob.glob(os.path.join(BASE, '*.csv')))
summary = []
for p in files:
    meta, t1, x1, t2, x2 = load(p)
    name = os.path.basename(p)
    tag = name.split('-')[0]
    print('=' * 110)
    print(name)
    print('  t 列一致: %s' % np.array_equal(t1, t2))
    for ch, x in (('CH1', x1), ('CH2', x2)):
        f, A = spec(x)
        b = burst_stats(x)
        # 频段能量（用 FFT 幅度量化，避免滤波器瞬态）
        bands = [(0, 1e3), (1e3, 10e3), (10e3, 100e3), (100e3, 1e6),
                 (1e6, 10e6), (10e6, 100e6), (100e6, 250e6)]
        be = []
        for lo, hi in bands:
            m = (f >= lo) & (f < hi)
            be.append(np.sqrt(np.sum(A[m] ** 2) / 2))  # 各频段 RMS
        print('  %s 频段RMS(mV): DC-1k=%.1f 1-10k=%.1f 10-100k=%.1f 100k-1M=%.1f '
              '1-10M=%.1f 10-100M=%.1f 100M+=%.1f'
              % (ch, *[v * 1e3 for v in be]))
        print('     peak@0-20k: ' + ', '.join('%.0fHz/%.0fmV' % (fr, a * 1e3)
              for fr, a in peaks(f, A, 700, 20e3, 6)))
        print('     peak@20k-1M: ' + ', '.join('%.0fkHz/%.1fmV' % (fr / 1e3, a * 1e3)
              for fr, a in peaks(f, A, 20e3, 1e6, 6)))
        print('     peak@1M-250M: ' + ', '.join('%.2fMHz/%.1fmV' % (fr / 1e6, a * 1e3)
              for fr, a in peaks(f, A, 1e6, 250e6, 6)))
        if b:
            print('     突发: >%.0fmV 事件=%d 点数占比=%.2f%% 宽度中位数=%.2fus 最大=%.2fus '
                  '幅度最大=%.2fV 间隔中位数=%.1fus'
                  % (b['thr'] * 1e3, b.get('n_events', 0), 100 * b.get('duty', 0),
                     b.get('width_us_med', 0), b.get('width_us_max', 0),
                     b.get('amp_max', 0), b.get('ia_med_us', 0)))
        summary.append((tag, ch, be, b))

    # 画图
    fig, ax = plt.subplots(2, 2, figsize=(15, 7))
    n = 50000  # 100us
    ax[0, 0].plot(t1 * 1e6, x1, lw=0.6); ax[0, 0].set_title('CH1 full'); ax[0, 0].set_xlabel('us')
    ax[0, 1].plot(t2 * 1e6, x2, lw=0.6, color='C1'); ax[0, 1].set_title('CH2 full'); ax[0, 1].set_xlabel('us')
    ax[1, 0].plot(t1[:n] * 1e6, x1[:n], lw=0.8); ax[1, 0].set_title('CH1 zoom 0-100us'); ax[1, 0].set_xlabel('us')
    ax[1, 1].plot(t2[:n] * 1e6, x2[:n], lw=0.8, color='C1'); ax[1, 1].set_title('CH2 zoom 0-100us'); ax[1, 1].set_xlabel('us')
    for a in ax.flat:
        a.grid(alpha=0.3)
    fig.suptitle(name)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'time_%s.png' % tag), dpi=90)
    plt.close(fig)

    fig, ax = plt.subplots(1, 2, figsize=(15, 4.5))
    for ch, x, c in (('CH1', x1, 'C0'), ('CH2', x2, 'C1')):
        f, A = spec(x)
        m = (f > 0) & (f < 120e3)
        ax[0].semilogy(f[m] / 1e3, A[m] * 1e3, lw=0.5, color=c, label=ch)
        m = (f > 100e3) & (f < 5e6)
        ax[1].semilogy(f[m] / 1e3, A[m] * 1e3, lw=0.5, color=c, label=ch)
    ax[0].set_xlabel('kHz'); ax[0].set_ylabel('mV'); ax[0].set_title('spectrum 0-120kHz')
    ax[1].set_xlabel('kHz'); ax[1].set_ylabel('mV'); ax[1].set_title('spectrum 100k-5MHz')
    for a in ax:
        a.grid(alpha=0.3, which='both'); a.legend()
    fig.suptitle(name)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'spec_%s.png' % tag), dpi=90)
    plt.close(fig)

print('\n完成，图已输出到 _analysis/')
