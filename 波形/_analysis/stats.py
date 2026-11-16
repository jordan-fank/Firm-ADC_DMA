# -*- coding: utf-8 -*-
"""6 份示波器 CSV 的基础统计 + 同时性检验 + 量化台阶估计。只读 CSV，不碰固件。"""
import os, sys, glob, json
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(path):
    meta = {}
    nskip = 0
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for i, line in enumerate(f):
            nskip += 1
            line = line.strip()
            if line.startswith('ch1_time'):
                break
            if ':' in line:
                k, v = line.split(':', 1)
                meta.setdefault(k.strip(), []).append(v.strip())
    data = np.loadtxt(path, delimiter=',', skiprows=nskip, encoding='utf-8-sig')
    return meta, data

def quant_step(x):
    """估计导出数据的量化台阶：相邻不同值差的中位数。"""
    xu = np.unique(x)
    if len(xu) < 3:
        return 0.0, len(xu)
    d = np.diff(xu)
    return float(np.median(d)), len(xu)

def ac(x):
    return x - x.mean()

files = sorted(glob.glob(os.path.join(BASE, '*.csv')))
out = []
for p in files:
    meta, d = load(p)
    t1, x1, t2, x2 = d[:, 0], d[:, 1], d[:, 2], d[:, 3]
    rec = {'file': os.path.basename(p), 'meta': {k: v for k, v in meta.items()}}
    for tag, x in (('ch1', x1), ('ch2', x2)):
        a = ac(x)
        step, nuniq = quant_step(x)
        rec[tag] = dict(
            mean=float(x.mean()), rms_ac=float(np.sqrt((a ** 2).mean())),
            std_ac=float(a.std()), ptp=float(x.max() - x.min()),
            min=float(x.min()), max=float(x.max()),
            p001=float(np.percentile(x, 0.1)), p999=float(np.percentile(x, 99.9)),
            p01=float(np.percentile(x, 1)), p99=float(np.percentile(x, 99)),
            quant_step=step, n_unique=int(nuniq),
            zero_frac=float((x == x.min()).mean()),
        )
    # 同时性/相关性检验（同文件内 CH1 vs CH2，零延迟 + 扫描延迟）
    a1, a2 = ac(x1), ac(x2)
    c0 = float(np.corrcoef(a1, a2)[0, 1])
    # 延迟扫描 ±2000 点（±4us）
    best = (-2.0, 0)
    lagmax = 2000
    for lag in range(-lagmax, lagmax + 1, 1):
        if lag < 0:
            c = np.corrcoef(a1[-lag:], a2[:lag])[0, 1]
        elif lag > 0:
            c = np.corrcoef(a1[:-lag], a2[lag:])[0, 1]
        else:
            c = c0
        if abs(c) > abs(best[0]):
            best = (float(c), lag)
    rec['xcorr'] = {'zero_lag': c0, 'best_corr': best[0], 'best_lag_pts': best[1],
                    'best_lag_ns': best[1] * 2.0}
    # 触发时间差
    ct1 = meta.get('Capture Time', [None, None])[0]
    ct2 = meta.get('Capture Time', [None, None])[1] if len(meta.get('Capture Time', [])) > 1 else None
    rec['capture_time'] = (ct1, ct2)
    out.append(rec)
    print('=' * 100)
    print(rec['file'])
    print('  时间轴: t=[%.6g, %.6g] s, 点数=%d' % (t1[0], t1[-1], len(t1)))
    print('  竖档: CH1=%s off=%s | CH2=%s off=%s' % (
        meta['Vertical Sensitivity'][0], meta['Vertical Offset'][0],
        meta['Vertical Sensitivity'][1], meta['Vertical Offset'][1]))
    for tag in ('ch1', 'ch2'):
        s = rec[tag]
        print('  %s: mean=%.4f V  ACrms=%.3f mV  ptp=%.3f V  min=%.3f max=%.3f  '
              '1-99%%=[%.3f,%.3f]  量化台阶=%.3f V (%d 个不同值)'
              % (tag.upper(), s['mean'], s['rms_ac'] * 1e3, s['ptp'], s['min'], s['max'],
                 s['p01'], s['p99'], s['quant_step'], s['n_unique']))
    print('  CH1-CH2: 零延迟相关=%.4f  最佳延迟=%+d 点 (%+.1f ns) 相关=%.4f'
          % (c0, best[1], best[1] * 2.0, best[0]))

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stats.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print('\n已写出 stats.json')
