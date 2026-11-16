"""
串口数据宽度降采样 + 图像生成工具

对每行的十六进制值按比例降采样，并生成对应的灰度PNG图像。
图像预处理流程与主程序保持一致：归一化 -> 高斯平滑 -> 对比度增强 -> 保存PNG。

用法:
    python downsample_serial.py <输入文件> [比例] [输出文件]

参数:
    输入文件  原始串口数据文件路径
    比例      保留比例，0~1之间的小数，默认 0.8（保留80%的值）
    输出文件  可选，默认在原文件名后加 _ds{比例} 后缀

示例:
    python downsample_serial.py serial_data.txt 0.5
    python downsample_serial.py serial_data.txt 0.8 output.txt
"""

import os
import numpy as np
from scipy.ndimage import gaussian_filter
from PIL import Image
import matplotlib.pyplot as plt


# ---------- 预处理函数（与 imaging_pipeline.py 保持一致） ----------

def read_hex_file(file_path: str) -> np.ndarray:
    data = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            for e in line.split():
                try:
                    data.append(int(e, 16))
                except ValueError:
                    continue
    return np.asarray(data, dtype=np.float64)


def preprocess_to_uint8(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return np.zeros((0,), dtype=np.uint8)
    mn, mx = float(x.min()), float(x.max())
    if mx > mn:
        x = (x - mn) / (mx - mn)
    else:
        x = np.zeros_like(x)
    return (x * 255).astype(np.uint8)


def smooth_image(data: np.ndarray, sigma: float = 3) -> np.ndarray:
    return gaussian_filter(data, sigma=sigma)


def enhance_contrast(data: np.ndarray, low_pct: float = 0, high_pct: float = 255, factor: float = 1) -> np.ndarray:
    p_low = np.percentile(data, low_pct)
    p_high = np.percentile(data, high_pct)
    out = data.copy()
    mask = (data >= p_low) & (data <= p_high)
    out[mask] = p_low + (data[mask] - p_low) * factor
    return np.clip(out, 0.0, 1.0)


# ---------- 降采样 ----------

def downsample_line(values: list, ratio: float) -> list:
    original = len(values)
    target = max(1, round(original * ratio))
    if target >= original:
        return values
    indices = [round(i * (original - 1) / (target - 1)) for i in range(target)] if target > 1 else [0]
    return [values[i] for i in indices]


# ---------- 主流程 ----------

ORIGINAL_WIDTH = 4000  # 主程序固定宽度


def run(input_path: str, ratio: float = 0.8, output_path: str = None):
    if not 0 < ratio <= 1:
        raise ValueError(f"比例必须在 (0, 1] 之间，当前值: {ratio}")

    base, ext = os.path.splitext(input_path)
    if output_path is None:
        output_path = f"{base}_ds{ratio}{ext}"
    img_path = os.path.splitext(output_path)[0] + ".png"

    # 读取所有值，按原始宽度4000切分成行
    raw = read_hex_file(input_path)
    if raw.size == 0:
        print("警告: 没有有效数据")
        return

    # 补齐到4000的整数倍
    remainder = raw.size % ORIGINAL_WIDTH
    if remainder != 0:
        raw = np.pad(raw, (0, ORIGINAL_WIDTH - remainder), constant_values=0)
    height = raw.size // ORIGINAL_WIDTH
    img_raw = raw.reshape((height, ORIGINAL_WIDTH))

    # 每行独立均匀降采样
    new_width = max(1, round(ORIGINAL_WIDTH * ratio))
    indices = [round(i * (ORIGINAL_WIDTH - 1) / (new_width - 1)) for i in range(new_width)]
    img_ds = img_raw[:, indices]  # shape: (height, new_width)

    # 写出降采样后的txt
    with open(output_path, "w", encoding="utf-8") as f:
        for row in img_ds:
            f.write(" ".join(f"{int(v):02X}" for v in row) + "\n")
    print(f"降采样完成: {height} 行，每行 {ORIGINAL_WIDTH} -> {new_width} 个值（比例 {ratio}）")
    print(f"txt 输出: {output_path}")

    # 预处理生成图像（与主程序一致）
    data_uint8 = preprocess_to_uint8(img_ds.flatten())
    data_norm = data_uint8.astype(np.float64) / 255.0
    # data_norm = smooth_image(data_norm, sigma=3)
    # data_norm = enhance_contrast(data_norm, low_pct=0, high_pct=100, factor=1)
    img2d = (np.clip(data_norm, 0.0, 1.0) * 255).astype(np.uint8).reshape((height, new_width))

    Image.fromarray(img2d).save(img_path)
    print(f"图像输出: {img_path}  ({new_width}x{height})")
    return img2d

if __name__ == "__main__":
    # ===== 在此修改参数 =====
    INPUT_FILE   = r"../测试数据/serial_20260601_104138.txt"
    RATIO        = 0.1      # 保留比例，例如 0.8 表示每行保留 80% 的值
    OUTPUT_FILE  = None   # None 表示自动命名（原文件名 + _ds{比例}）
    ASPECT_RATIO = 0.05   # matplotlib 显示比例：pixel_height/pixel_width，<1 纵向拉伸
    # =======================

    img2d = run(INPUT_FILE, RATIO, OUTPUT_FILE)
    if img2d is not None:
        plt.figure(figsize=(12, 6))
        plt.imshow(img2d, cmap="gray", aspect=ASPECT_RATIO)
        plt.axis("off")
        plt.tight_layout()
        plt.show()
