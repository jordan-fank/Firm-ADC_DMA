# -*- coding: utf-8 -*-
"""三种 ADC 采样模型对比：平均型 / 瞬时型 / 短孔径型。"""
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
x = load(os.path.join(BASE,'C-B + ZXY 伺服使能.csv'))
x = x - x.mean()
dur = len(x)/FS
n_out = int(dur*FADC)
t_out = np.arange(n_out)/FADC

def model_average(x, ap_s):
    n=max(1,int(round(ap_s*FS)))
    xs=signal.lfilter(np.ones(n)/n,1.0,x)
    return np.interp(t_out*FS, np.arange(len(xs)), xs)

def model_instant(x):
    return np.interp(t_out*FS, np.arange(len(x)), x)

def stats(y, name):
    w=int(round(FADC/8e3))
    seg=y[:len(y)//w*w].reshape(-1,w)
    wave=seg.mean(axis=0)
    pp=(wave.max()-wave.min())*1e3
    # 100Hz 成分
    b,a=signal.butter(4,[90/(FADC/2),110/(FADC/2)],'band')
    h100=np.sqrt(np.mean(signal.filtfilt(b,a,y)**2))*1e3
    # 8k 折叠图案的 RMS（去掉均值）
    rms=np.sqrt(np.mean((wave-wave.mean())**2))*1e3
    # 0-97k 总 RMS
    b,a=signal.butter(4,95e3/(FADC/2),'low')
    tot=np.sqrt(np.mean(signal.filtfilt(b,a,y)**2))*1e3
    print('%-22s 8k-fold pp=%7.1f mV (%5.2f LSB)  图案RMS=%6.1f mV  100Hz=%6.2f mV  总RMS=%6.2f mV'
          % (name, pp, pp/LSB, rms, h100, tot))

stats(model_instant(x), '瞬时采样(无孔径)')
stats(model_average(x, 0.1e-6), '孔径100ns')
stats(model_average(x, 0.5e-6), '孔径500ns')
stats(model_average(x, 1.556e-6), '孔径1.556us(标称)')

# 瞬时采样下的尖峰命中统计
y=model_instant(x)
d=np.diff(y)
print('\n瞬时采样点间跳变: p50=%.1f mV, p99=%.1f mV, max=%.1f mV'
      % (np.percentile(abs(d),50)*1e3, np.percentile(abs(d),99)*1e3, abs(d).max()*1e3))
# 有多少点超过 6 LSB 跳变
print('跳变 >6 LSB(77mV) 的点数: %d / %d (%.2f%%)'
      % ((abs(d)>6*LSB).sum(), len(d), (abs(d)>6*LSB).mean()*100))
