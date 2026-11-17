# -*- coding: utf-8 -*-
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
BASE=r'E:\Desktop\MCU_revision1\排查波形'; FS=50e6; FADC=194e3; LSB=3.3/256

def dec(x):
    n=int(len(x)/FS*FADC); t=np.arange(n)/FADC
    return np.interp(t*FS,np.arange(len(x)),x)
def notch(sig,fs,f0=8e3,Q=40,nh=12):
    z=sig.astype(float)
    for k in range(1,nh+1):
        if k*f0>=fs/2*0.95: break
        b,a=signal.iirnotch(k*f0,Q,fs); z=signal.filtfilt(b,a,z)
    return z

print('%-22s %-18s %-18s' % ('数据','0-20kHz RMS 前->后','0-95kHz RMS 前->后'))
for tag,fn in [('A 仅传感器','A-仅给传感器供电测PA2.csv'),
               ('E 真实工况(探针转)','E-D + 按真实工况让探针转起来.csv')]:
    x=load(os.path.join(BASE,fn)); x=x-x.mean(); y=dec(x); z=notch(y,FADC)
    out=[]
    for hi in (20e3, 95e3):
        b,a=signal.butter(4,hi/(FADC/2),'low')
        yl=signal.filtfilt(b,a,y); zl=signal.filtfilt(b,a,z)
        out.append('%6.3f -> %6.3f LSB (%.1f%%)' %
                   (yl.std()/LSB, zl.std()/LSB, (zl.std()/yl.std()-1)*100))
    print('%-22s %-18s %-18s' % (tag, out[0], out[1]))

# 若工件真有 165 周期/转 的特征，会被误伤多少？
print('\n--- 误伤测试：叠加工件特征后陷波 ---')
x=load(os.path.join(BASE,'A-仅给传感器供电测PA2.csv')); x=x-x.mean()
y=dec(x)
R=4000
t=np.arange(len(y))/FADC
for k_per_rev in (10, 50, 165, 300):
    feat=2.0*LSB*np.sin(2*np.pi*k_per_rev*t/(R/FADC))   # 2 LSB 幅度的特征
    z=notch(y+feat,FADC)
    z0=notch(y,FADC)
    # 用陷波后减陷波后基线，提取特征残余
    resid=z-z0
    amp=(resid.max()-resid.min())/2/LSB
    print('  特征 %3d 周期/行: 输入 2.00 LSB -> 残余 %.3f LSB (%+.1f%%)'
          % (k_per_rev, amp, (amp/2-1)*100))
