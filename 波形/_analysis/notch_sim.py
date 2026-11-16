# -*- coding: utf-8 -*-
"""模拟真实 ADC(194k, 1.556us孔径, 无抗混叠) + 梳状陷波，量化图像条纹剩余幅度。"""
import os
import numpy as np, pandas as pd
from scipy import signal

def load(p):
    n = 0
    with open(p,'r',encoding='utf-8-sig',errors='replace') as f:
        for line in f:
            n += 1
            if line.strip().startswith('ch2_time'): break
    df = pd.read_csv(p, skiprows=n, header=None, names=['t','x'],
                     encoding='utf-8-sig', engine='c')
    return df['t'].to_numpy(), df['x'].to_numpy()

BASE = r'E:\Desktop\MCU_revision1\排查波形'
FS_OSC = 50e6
FS_ADC = 194e3
LSB = 3.3/256

def adc_model(x, fs_in, fs_out, aperture_s):
    """boxcar 孔径 + 无抗混叠抽取（模拟 F407 ADC 采样保持）"""
    n_ap = max(1, int(round(aperture_s*fs_in)))
    ap = np.ones(n_ap)/n_ap
    xs = signal.lfilter(ap, 1.0, x)
    dur = len(x)/fs_in
    n_out = int(dur*fs_out)
    t_out = np.arange(n_out)/fs_out
    idx = t_out*fs_in
    return np.interp(idx, np.arange(len(xs)), xs)

def fold_pp(x, per):
    n = int(round(per))
    m = len(x)//n
    if m < 2: return 0.0
    seg = x[:m*n].reshape(m,n)
    w = seg.mean(axis=0)
    return (w.max()-w.min())*1e3

def comb_notch(x, fs, f0, Q=40, n_harm=12):
    y = x.astype(float)
    for k in range(1, n_harm+1):
        fk = k*f0
        if fk >= fs/2*0.95: break
        b,a = signal.iirnotch(fk, Q, fs)
        y = signal.filtfilt(b,a,y)
    return y

t, x = load(os.path.join(BASE, 'C-B + ZXY 伺服使能.csv'))
x = x - x.mean()

# 1) 模拟 ADC
y = adc_model(x, FS_OSC, FS_ADC, 1.556e-6)
print('ADC 模拟输出: %d 点, %.1f ms' % (len(y), len(y)/FS_ADC*1e3))

# 2) 原始 8kHz 幅度（对 125us 折叠）
w = int(round(FS_ADC/8e3))
seg = y[:len(y)//w*w].reshape(-1,w)
wave = seg.mean(axis=0)
print('\n[原始] 8kHz 折叠峰峰值 = %.1f mVpp = %.1f LSB' %
      ((wave.max()-wave.min())*1e3, (wave.max()-wave.min())/LSB))

# 3) 图像条纹：每转 4000 点，2910rpm -> 每点间隔, 8k/194k=24.25 点/周期
print('[原始] 图像条纹(24.25点周期) = %.1f mVpp' % fold_pp(y - y.mean(), FS_ADC/8e3))

# 4) 陷波
z = comb_notch(y, FS_ADC, 8e3, Q=40, n_harm=12)
wave2 = z[:len(z)//w*w].reshape(-1,w).mean(axis=0)
print('\n[陷波后] 8kHz 折叠峰峰值 = %.1f mVpp = %.1f LSB' %
      ((wave2.max()-wave2.min())*1e3, (wave2.max()-wave2.min())/LSB))
print('[陷波后] 图像条纹(24.25点周期) = %.1f mVpp' % fold_pp(z - z.mean(), FS_ADC/8e3))

# 5) 残余频谱：陷波前后 0-97k 的 PSD 峰值
f, P0 = signal.welch(y, FS_ADC, nperseg=4096)
f, P1 = signal.welch(z, FS_ADC, nperseg=4096)
print('\n%-8s %-12s %-12s %-10s' % ('freq','before mVrms','after mVrms','atten dB'))
for k in [1,2,3,4,6,8,10,12]:
    fk = 8e3*k
    if fk > FS_ADC/2: continue
    i = np.argmin(abs(f-fk))
    a0 = np.sqrt(P0[max(0,i-5):i+6].max())
    a1 = np.sqrt(P1[max(0,i-5):i+6].max())
    if a0 < 1e-9: continue
    print('%-8s %-12.4f %-12.4f %-10.1f' % ('%.0fk'%(fk/1e3), a0*1e3, a1*1e3, 20*np.log10(a1/a0)))

# 6) 带内总能量变化
def band_rms(sig, fs, lo, hi):
    b,a = signal.butter(4, [lo/(fs/2), hi/(fs/2)], 'band')
    return np.sqrt(np.mean(signal.filtfilt(b,a,sig)**2))*1e3
print('\n0-97kHz 带内 RMS: 原始 %.2f mV -> 陷波后 %.2f mV' %
      (band_rms(y, FS_ADC, 10, 95000), band_rms(z, FS_ADC, 10, 95000)))
