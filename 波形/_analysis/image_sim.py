# -*- coding: utf-8 -*-
"""按真实图像组织(4000点/行)量化条纹幅度，对比陷波前后。"""
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
x=load(os.path.join(BASE,'C-B + ZXY 伺服使能.csv')); x=x-x.mean()
n_out=int(len(x)/FS*FADC); t_out=np.arange(n_out)/FADC
y=np.interp(t_out*FS,np.arange(len(x)),x)          # 瞬时采样模型

def comb_notch(sig,fs,f0=8e3,Q=40,nh=12):
    z=sig.astype(float)
    for k in range(1,nh+1):
        if k*f0>=fs/2*0.95: break
        b,a=signal.iirnotch(k*f0,Q,fs); z=signal.filtfilt(b,a,z)
    return z
z=comb_notch(y,FADC)

def row_stats(sig,name):
    R=4000
    m=len(sig)//R
    rows=sig[:m*R].reshape(m,R)
    # 固定图案 = 行平均；随机 = 行减平均
    pat=rows.mean(axis=0)
    resid=rows-pat
    print('%-10s 行数=%3d | 单行RMS=%5.2f LSB | 固定图案pp=%5.2f LSB | 图案RMS=%5.2f LSB | 图案/随机=%.2f'
          % (name, m, rows.std()/LSB, (pat.max()-pat.min())/LSB,
             pat.std()/LSB, pat.std()/max(resid.std(),1e-12)))
    # 条纹空间频率
    F=np.fft.rfft(pat-pat.mean())
    k=np.argmax(abs(F[1:]))+1
    print('           最大条纹分量: %d 周期/行 (周期 %.2f px), 幅度 %.2f LSB'
          % (k, R/k, abs(F[k])*2/R/LSB))
    return pat

p0=row_stats(y,'原始')
p1=row_stats(z,'陷波后')

# 对照：仅传感器 A 组（真正"干净"的基准）
xa=load(os.path.join(BASE,'A-仅给传感器供电测PA2.csv')); xa=xa-xa.mean()
na=int(len(xa)/FS*FADC); ta=np.arange(na)/FADC
ya=np.interp(ta*FS,np.arange(len(xa)),xa)
row_stats(ya,'A组基准')
