# -*- coding: utf-8 -*-
import numpy as np
from scipy import signal

h = np.array([
0,0,0,0,0,0,0,0,0,1,1,0,0,-1,-2,-3,-3,-3,-1,2,6,10,13,12,8,0,-12,-24,-34,-38,
-33,-17,10,42,72,92,93,69,21,-46,-119,-180,-209,-191,-120,0,149,296,402,431,
358,175,-99,-417,-713,-906,-922,-701,-218,511,1424,2425,3391,4196,4729,4914,
4729,4196,3391,2425,1424,511,-218,-701,-922,-906,-713,-417,-99,175,358,431,
402,296,149,0,-120,-191,-209,-180,-119,-46,21,69,93,92,72,42,10,-17,-33,-38,
-34,-24,-12,0,8,12,13,10,6,2,-1,-3,-3,-3,-2,-1,0,0,1,1,0,0,0,0,0,0,0,0], dtype=float)

fs = 200000.0
h = h / h.sum()          # 归一化，0 Hz 增益 = 1
w, H = signal.freqz(h, worN=1<<16, fs=fs)
mag_db = 20*np.log10(np.abs(H) + 1e-12)

def at(f):
    i = np.argmin(np.abs(w-f))
    return mag_db[i]

print('taps=%d  DC=%.4f' % (len(h), h.sum()))
print('%-10s %s' % ('freq', 'atten(dB)'))
for f in [0, 1000, 5000, 8000, 10000, 12000, 15000, 16000, 20000, 24000, 32000, 40000, 48000, 64000, 80000, 96000]:
    print('%-10s %7.2f' % ('%d Hz'%f, at(f)))

# -6dB / -20dB / -40dB 点
for target in (-3, -6, -20, -40, -60):
    idx = np.argmax((mag_db < target) & (w > 1000))
    print('first below %d dB at %.0f Hz' % (target, w[idx]))

# 通带纹波
passband = mag_db[(w>=0)&(w<=10000)]
print('passband 0-10kHz ripple: %.3f dB (min %.3f max %.3f)' %
      (passband.max()-passband.min(), passband.min(), passband.max()))
