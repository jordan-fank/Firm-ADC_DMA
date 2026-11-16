# -*- coding: utf-8 -*-
import os
import numpy as np, pandas as pd
from scipy import signal

def load(p):
    n = 0
    with open(p, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            n += 1
            if line.strip().startswith('ch2_time'):
                break
    df = pd.read_csv(p, skiprows=n, header=None, names=['t','x'],
                     encoding='utf-8-sig', engine='c')
    return df['t'].to_numpy(), df['x'].to_numpy()

BASE = r'E:\Desktop\MCU_revision1\排查波形'
FS = 50e6
cases = [('A', 'A-仅给传感器供电测PA2.csv'), ('C', 'C-B + ZXY 伺服使能.csv')]

for tag, fn in cases:
    t, x = load(os.path.join(BASE, fn))
    x = x - x.mean()
    print('\n===== %s =====' % tag)
    f, P = signal.welch(x, FS, nperseg=1<<16, noverlap=1<<15)
    floor = np.median(P[(f>1e6)&(f<20e6)])
    print('noise floor(1-20MHz)=%.3e V^2/Hz' % floor)
    line = []
    for k in [1,2,3,4,5,10,20,50,100,200,500,1000,2000,3000,4000,5000]:
        fk = 8e3*k
        if fk > 24e6: continue
        idx = np.argmin(np.abs(f-fk))
        pk = P[max(0,idx-3):idx+4].max()
        amp = np.sqrt(pk*(f[1]-f[0]))*1e3
        line.append('%d:%.3f' % (k, amp))
    print('harm mVrms  ' + '  '.join(line))
    for k in range(1, 6001):
        fk = 8e3*k
        if fk > 24e6: break
        idx = np.argmin(np.abs(f-fk))
        pk = P[max(0,idx-3):idx+4].max()
        if pk < floor*10:
            print('harmonic drops to <10x floor at k=%d (%.0f kHz)' % (k, fk/1e3))
            break
    cum = np.cumsum(P)/P.sum()
    for frac in (0.5, 0.9, 0.99):
        i = np.searchsorted(cum, frac)
        print('  %d%% energy below %.1f kHz' % (frac*100, f[i]/1e3))
