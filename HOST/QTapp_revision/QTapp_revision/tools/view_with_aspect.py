# -*- coding: utf-8 -*-
import sys
import os
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image

# ===== 在此修改参数 =====
DATA_FILE  = r"../测试数据/serial_20260601_104138.txt"  # hex数据文件路径（命令行参数优先）
WIDTH      = 4000   # 每行像素数
FIG_WIDTH  = 3     # 显示宽度（英寸）
FIG_HEIGHT = 4      # 显示高度（英寸），调大则纵向拉伸
# =======================

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DATA_FILE
    if not path:
        print("请设置 DATA_FILE 或通过命令行传入文件路径")
        sys.exit(1)

    values = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            for tok in line.strip().split():
                try:
                    values.append(int(tok, 16))
                except ValueError:
                    pass
    raw = np.array(values, dtype=np.uint16)

    mn, mx = float(raw.min()), float(raw.max())
    data = (raw - mn) / (mx - mn) if mx > mn else np.zeros_like(raw, dtype=np.float64)
    data_u8 = (np.clip(data, 0.0, 1.0) * 255).astype(np.uint8)

    rem = data_u8.size % WIDTH
    if rem:
        data_u8 = np.pad(data_u8, (0, WIDTH - rem), constant_values=0)
    img = data_u8.reshape(-1, WIDTH)
    print(f"图像尺寸: {img.shape[0]} 行 × {img.shape[1]} 列")

    out_path = os.path.splitext(path)[0] + "_aspect.png"
    plt.figure(figsize=(FIG_WIDTH, FIG_HEIGHT))
    plt.imshow(img, cmap="gray", aspect="auto", interpolation="nearest")
    plt.axis("off")
    plt.savefig(out_path, bbox_inches="tight", pad_inches=0, dpi=100)
    print(f"图像已保存: {out_path}")
    plt.show()
