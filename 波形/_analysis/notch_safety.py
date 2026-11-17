# -*- coding: utf-8 -*-
"""验证陷波对'干净数据'的破坏程度：A组(仅传感器)与C组的低频信号。"""
import os
import numpy as np, pandas as pd
from scipy import signal

def load(p):
    n=0
    with open(p,'r',encoding='utf-8-sig',errors='replace') as f:
        for line in f:
            n+=1
            if line.strip().startswith('ch2_time'): break
    df=pd.read_csv(p,skiprows=n,header=None,names=['t','x'],
                   encoding='utf-8-sig',engine='c')
    return df['x'].to_numpy().astype(float)

BASE=r'E:\Desktop\MCU_revision1\排查波形'
FS=50e6; FADC=194e3; LSB=3.3/256

def dec(x):
    n=int(len(x)/FS*FADC); t=np.arange(n)/FADC
    return np.interp(t*FS,np.arange(len(x)),x)

def notch(sig,fs,f0=8e3,Q=40,nh=12):
    z=sig.astype(float)
    for k in range(1,nh+1):
        if k*f0>=fs/2*0.95: break
        b,a=signal.iirnotch(k*f0,Q,fs); z=signal.filtfilt(b,a,z)
    return z

for tag,fn in [('A 仅传感器(干净)','A-仅给传感器供电测PA2.csv'),
               ('C 伺服使能','C-B + ZXY 伺服使能.csv')]:
    x=load(os.path.join(BASE,fn)); x=x-x.mean(); y=dec(x); z=notch(y,FADC)
    d=z-y
    print('\n=== %s ===' % tag)
    print('  RMS: 前 %.2f LSB -> 后 %.2f LSB (变化 %.2f%%)'
          % (y.std()/LSB, z.std()/LSB, (z.std()/y.std()-1)*100))
    print('  最大单点偏差 |z-y|: %.2f LSB' % (abs(d).max()/LSB))
    print('  相关系数 corr(y,z): %.6f' % np.corrcoef(y,z)[0,1])
    # 低频(有用)成分是否被改
    b,a=signal.butter(4,5e3/(FADC/2),'low')
    yl=signal.filtfilt(b,a,y); zl=signal.filtfilt(b,a,z)
    print('  0-5kHz 低频成分变化: RMS %.3f -> %.3f LSB, max diff %.4f LSB'
          % (yl.std()/LSB, zl.std()/LSB, abs(zl-yl).max()/LSB))
    # 差异信号的频谱：能量集中在哪
    f,P=signal.welch(d,FADC,nperseg=4096)
    tot=P.sum()
    for lo,hi in [(0,7.9e3),(7.9e3,8.1e3),(8.1e3,15.9e3),(15.9e3,16.1e3),(16.1e3,97e3)]:
        m=(f>=lo)&(f<hi)
        print('    差异能量 %5.1f-%5.1f kHz: %5.1f%%' % (lo/1e3,hi/1e3,P[m].sum()/tot*100))
