import numpy as np
import time
import logging

logger = logging.getLogger(__name__)

from utils.speed_frame_filter import strip_speed_frames


try:
    from numpy.lib.stride_tricks import sliding_window_view
except ImportError:
    sliding_window_view = None


def median_filter_1d(x, ksize=3):
    """
    纯 numpy 实现的一维中值滤波，替代 scipy.ndimage.median_filter。
    x: 一维数组
    ksize: 窗口大小（奇数）
    """
    x = np.asarray(x, dtype=np.float64)

    if ksize <= 1 or x.size == 0:
        return x.copy()

    if ksize % 2 == 0:
        ksize += 1  # 保证为奇数

    pad = ksize // 2
    x_pad = np.pad(x, (pad, pad), mode="edge")

    # 优先使用 sliding_window_view
    if sliding_window_view is not None:
        windows = sliding_window_view(x_pad, ksize)  # shape: (N, ksize)
        med = np.median(windows, axis=-1)
        return med.astype(np.float64)

    # 兼容老版本 numpy：用循环实现
    out = np.empty_like(x, dtype=np.float64)
    for i in range(x.size):
        window = x_pad[i : i + ksize]
        out[i] = np.median(window)
    return out


def read_data_from_file(file_path):
    """
    从指定的文件中读取16进制数据，转换为十进制数值。

    返回:
        data (numpy.ndarray): 一维数组 (float64)
    """
    data = []
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    for line in lines:
        elements = line.split()
        for element in elements:
            try:
                decimal_value = int(element, 16)
                data.append(decimal_value)
            except ValueError:
                # 如果有不是合法16进制的字符串，直接忽略
                continue

    data, speed_frames = strip_speed_frames(data)
    if speed_frames:
        logger.info(
            "[read] removed %d speed frame(s) before 3D processing, first rpm=%d",
            len(speed_frames),
            speed_frames[0]["rpm"],
        )

    return np.array(data, dtype=np.float64)


def smooth_image2(data, ksize=3, selective=False, thr=3.5):
    """
    使用中值滤波去噪（保持尺寸不变）

    参数:
      data      : 1D 数组（当前流程里只会传一维）
      ksize     : 中值滤波窗口（奇数）
      selective : True 时仅替换异常点（这里也支持）
      thr       : 选择性模式下阈值

    返回:
      平滑后的数组 (float64)
    """
    x = np.asarray(data, dtype=np.float64)

    # 普通中值滤波：所有点都平滑
    if not selective:
        return median_filter_1d(x, ksize=ksize)

    # 选择性中值滤波：只替换“异常点”
    med = np.median(x)
    mad = np.median(np.abs(x - med)) + 1e-12  # 防止除0
    z = 0.6745 * (x - med) / mad
    mask = np.abs(z) > thr  # 超过阈值视为异常点

    mf = median_filter_1d(x, ksize=ksize)
    out = x.copy()
    out[mask] = mf[mask]
    return out


def preprocess_data(data):
    """
    归一化 + 中值去噪 + Gamma 校正
    尺寸不变，只改变每个点的数值，最终输出 uint8（0~255）
    """
    data_log = np.asarray(data, dtype=np.float64)

    # 归一化到 [0,1]
    min_val = np.min(data_log)
    max_val = np.max(data_log)
    if max_val > min_val:
        data_normalized = (data_log - min_val) / (max_val - min_val)
    else:
        data_normalized = np.zeros_like(data_log)

    # 中值滤波去噪（1D）
    data_normalized = smooth_image2(data_normalized, ksize=5, selective=False)

    # Gamma 校正（此处 gamma=1.0，相当于不变，为以后预留）
    gamma = 1.0
    data_normalized = np.clip(data_normalized, 0.0, 1.0)
    data_normalized = np.power(data_normalized, float(gamma))
    data_normalized = np.clip(data_normalized, 0.0, 1.0)

    # 映射到 [0,255] 并转成 uint8
    return (data_normalized * 255).astype(np.uint8)


def data_to_strip_2d(data_1d, width=4000):
    """
    将 1D 数据按 width 重排为 (H, width)，严格保持像素数，不插值。

    如果长度不是 width 的整数倍，会在末尾用 0 padding 补齐。
    """
    data_1d = np.asarray(data_1d)

    remainder = len(data_1d) % width
    if remainder != 0:
        padding_size = width - remainder
        data_1d = np.pad(data_1d, (0, padding_size), mode='edge')

    height = len(data_1d) // width
    data_2d = data_1d.reshape((height, width))

    logger.debug(f"展开图尺寸：height={height}, width={width}")
    return data_2d


def image_to_cylinder_inner_surface(data_2d,
                                    radius=1.0,
                                    z_min=0.0,
                                    z_max=None,
                                    isotropic_spacing=True,
                                    expected_width=4000,
                                    radius_min_ratio=0.6):
    """
    将 (H, W) 的展开图映射到圆筒坐标：

      - W = expected_width：一圈，左右首尾相接
      - 行索引 v=0（二维最上边） → z = z_max_eff（3D 最上边）
      - v=H-1（二维最下边）     → z = z_min（3D 最下边）

    半径不再固定为常数 radius，而是：
        radius_min = radius_min_ratio * radius
        V_norm = (V - v_min) / (v_max - v_min)
        R = radius_min + (radius - radius_min) * V_norm
    """
    H, W = data_2d.shape
    assert W == expected_width, f"宽度应为 {expected_width}，目前为 {W}"

    # 像素索引
    u = np.arange(W)[None, :]   # (1, W)
    v = np.arange(H)[:, None]   # (H, 1)

    # 角度：一圈 W 像素 -> 0~2π（逆绘制）
    # theta_1d = 2.0 * np.pi * u / W           # (1, W)
    # 角度：一圈 W 像素 -> 2π~0（顺绘制，与采集方向一致）
    theta_1d = 2.0 * np.pi * (W - 1 - u) / W
    
    theta = np.repeat(theta_1d, H, axis=0)   # (H, W)

    # ===== 1. 计算 z 方向 =====
    if isotropic_spacing:
        # 周向弧长间距：Δs_xy = 2πr / W
        dz_xy = 2.0 * np.pi * radius / W

        if H > 1:
            # 让 Δz = Δs_xy
            z_max_eff = z_min + dz_xy * (H - 1)
        else:
            z_max_eff = z_min
    else:
        if z_max is None:
            z_max_eff = z_min + 1.0
        else:
            z_max_eff = z_max

    # 高度插值：v=0 -> z_max_eff, v=H-1 -> z_min
    if H > 1:
        z_line = z_max_eff - (z_max_eff - z_min) * v / (H - 1)  # (H, 1)
    else:
        z_line = np.full((H, 1), (z_min + z_max_eff) / 2.0)

    Z = np.repeat(z_line, W, axis=1)  # (H, W)

    # ===== 2. 利用灰度值控制半径 =====
    V = data_2d.astype(np.float32)    # (H, W)

    v_min = V.min()
    v_max = V.max()
    if v_max > v_min:
        V_norm = (V - v_min) / (v_max - v_min)   # (H, W), ∈ [0,1]
    else:
        V_norm = np.zeros_like(V)

    radius_min = radius_min_ratio * radius
    R = radius_min + (radius - radius_min) * V_norm  # (H, W)

    X = R * np.cos(theta)
    Y = R * np.sin(theta)

    return X, Y, Z, V


def save_point_cloud_xyz(X, Y, Z, V, xyz_path=None):
    """
    将圆筒数据导出为点云文本 (XYZ + intensity)，每行:
      x  y  z  intensity

    Args:
        X, Y, Z: 坐标数组
        V: 灰度值数组
        xyz_path: 输出路径，如果为None则自动生成带时间戳的文件名
    """
    if xyz_path is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        xyz_path = f"cylinder_points_{timestamp}.xyz"

    x_flat = X.ravel()
    y_flat = Y.ravel()
    z_flat = Z.ravel()
    v_flat = V.ravel()

    pts = np.column_stack([x_flat, y_flat, z_flat, v_flat])
    np.savetxt(xyz_path, pts, fmt="%.6f %.6f %.6f %.3f")
    logger.info(f"点云已保存为 XYZ 文件: {xyz_path}")
    logger.info(f"[3D点云] 保存XYZ文件: {xyz_path}")


def run_pipeline_3d(file_path,
                    width=4000,
                    radius_inner=1.0,
                    z_min=0.0,
                    xyz_path="cylinder_points.xyz",
                    isotropic_spacing=True,
                    radius_min_ratio=0.2,
                    flip_horizontal=False,
                    ds_ratio=1.0):
    """
    从文件 → 预处理 → 2D 展开 → 3D 圆筒点云 → 保存 XYZ 点云文件。
    """
    logger.info(f"从文件读取数据: {file_path}")
    raw = read_data_from_file(file_path)

    logger.debug("数据预处理 ...")
    data_uint8 = preprocess_data(raw)

    logger.debug("数据展开 ...")
    data_2d = data_to_strip_2d(data_uint8, width=width)

    if ds_ratio < 1.0:
        target_w = max(1, round(width * ds_ratio))
        indices = [round(i * (width - 1) / (target_w - 1)) for i in range(target_w)]
        data_2d = data_2d[:, indices]
        width = target_w

    if flip_horizontal:
        logger.debug("用户使用了翻转功能，生成点云会进行翻转 ...")
        data_2d = np.fliplr(data_2d)  # 等价于 [:, ::-1]

    logger.debug("映射到圆筒坐标（半径由灰度控制）...")
    X, Y, Z, V = image_to_cylinder_inner_surface(
        data_2d,
        radius=radius_inner,
        z_min=z_min,
        z_max=None,
        isotropic_spacing=isotropic_spacing,
        expected_width=width,
        radius_min_ratio=radius_min_ratio
    )

    logger.debug("保存点云 ...")
    save_point_cloud_xyz(X, Y, Z, V, xyz_path=xyz_path)

    logger.info("流程完成。")


def generate_3d_for_viewer(file_path,
                           width=4000,
                           radius_inner=1.0,
                           z_min=0.0,
                           isotropic_spacing=True,
                           radius_min_ratio=0.8,
                           flip_horizontal=False,
                           max_points=None):
    """
    专门用于 UI Viewer 的 3D 生成函数：
    - 只返回内存数据，供 OpenGL 直接使用
    - 不保存文件（节省 I/O 时间，提升响应速度）
    - 支持预览优化：通过 max_points 参数控制点数（不影响保存的文件）

    返回:
        positions: (N, 3) float32 - xyz 坐标
        colors:    (N, 3) float32 - RGB 颜色 (0~1)
        center:    (3,)   float32 - 点云中心
        radius:    float           - 点云半径
    """
    logger.info(f"从文件读取数据: {file_path}")
    raw = read_data_from_file(file_path)

    logger.debug("数据预处理 ...")
    data_uint8 = preprocess_data(raw)

    logger.debug("数据展开 ...")
    data_2d = data_to_strip_2d(data_uint8, width=width)

    if flip_horizontal:
        logger.debug("应用水平翻转 ...")
        data_2d = np.fliplr(data_2d)

    logger.debug("映射到圆筒坐标（半径由灰度控制）...")
    X, Y, Z, V = image_to_cylinder_inner_surface(
        data_2d,
        radius=radius_inner,
        z_min=z_min,
        z_max=None,
        isotropic_spacing=isotropic_spacing,
        expected_width=width,
        radius_min_ratio=radius_min_ratio
    )

    # ===== 预览优化：在生成阶段就进行抽样（仅预览，不影响保存） =====
    H, W = X.shape
    total_points = H * W

    # 从外部传入 max_points 参数，如果超过则进行随机抽样
    if max_points is not None and total_points > max_points:
        # 使用随机采样（比均匀采样更好地保留细节和特征）
        # 先展平所有数组
        X_flat = X.ravel()
        Y_flat = Y.ravel()
        Z_flat = Z.ravel()
        V_flat = V.ravel()

        # 随机选择索引
        indices = np.random.choice(total_points, max_points, replace=False)

        # 应用采样
        X_sampled = X_flat[indices]
        Y_sampled = Y_flat[indices]
        Z_sampled = Z_flat[indices]
        V_sampled = V_flat[indices]

        logger.debug(f"预览优化：从 {total_points} 点随机采样到 {max_points} 点")
        logger.debug(f"注意：这是预览数据，保存的文件仍然是完整的 {total_points} 点")

        # 组织返回数据（已经是一维数组）
        positions = np.column_stack([
            X_sampled, Y_sampled, Z_sampled
        ]).astype(np.float32)

        v_norm = np.clip(V_sampled, 0, 255) / 255.0
        colors = np.column_stack([v_norm, v_norm, v_norm]).astype(np.float32)
    else:
        # 不需要采样，直接使用完整数据
        positions = np.column_stack([
            X.ravel(), Y.ravel(), Z.ravel()
        ]).astype(np.float32)

        v_flat = V.ravel().astype(np.float32)
        v_norm = np.clip(v_flat, 0, 255) / 255.0
        colors = np.column_stack([v_norm, v_norm, v_norm]).astype(np.float32)

    # 计算中心和半径
    min_xyz = positions.min(axis=0)
    max_xyz = positions.max(axis=0)
    center = ((min_xyz + max_xyz) / 2.0).astype(np.float32)
    radius = float(np.linalg.norm(max_xyz - min_xyz) / 2.0)
    if radius <= 0:
        radius = 1.0

    logger.debug("3D 数据生成完成（仅内存，无文件保存）")
    return positions, colors, center, radius


def generate_3d_for_web(xyz_file_path, max_points=50000):
    """
    专门用于Web预览的点云加载函数：
    - 从已保存的 .xyz 文件加载
    - 进行高效抽样，减少传输数据量
    - 返回JSON格式的点云数据

    参数:
        xyz_file_path: .xyz 文件路径
        max_points: 最大点数（默认5万）

    返回:
        dict: {
            "positions": [[x,y,z], ...],
            "colors": [[r,g,b], ...],
            "total_points": 原始点数,
            "sampled_points": 抽样后点数
        }
    """
    logger.debug(f"加载点云文件: {xyz_file_path}")

    # 1. 快速读取文件（使用 numpy）
    data = np.loadtxt(xyz_file_path, dtype=np.float32)

    total_points = data.shape[0]
    logger.debug(f"原始点数: {total_points}")

    # 2. 如果点数超过限制，进行均匀抽样
    if total_points > max_points:
        # 均匀抽样索引
        step = int(np.ceil(total_points / max_points))
        data = data[::step, :]
        logger.debug(f"预览优化：从 {total_points} 点抽样到 {data.shape[0]} 点（步长={step}）")

    # 3. 提取坐标和颜色
    positions = data[:, 0:3].tolist()  # x, y, z
    gray = data[:, 3]  # 灰度值
    gray_norm = np.clip(gray, 0, 255) / 255.0
    colors = np.stack([gray_norm, gray_norm, gray_norm], axis=1).tolist()

    return {
        "positions": positions,
        "colors": colors,
        "total_points": int(total_points),
        "sampled_points": len(positions)
    }


def main():
    file_path = "data02.txt"
    radius_inner = 1.0
    z_min = 0.0
    xyz_path = "cylinder_points.xyz"

    run_pipeline_3d(
        file_path=file_path,
        width=4000,
        radius_inner=radius_inner,
        z_min=z_min,
        xyz_path=xyz_path,
        isotropic_spacing=True,
        radius_min_ratio=0.8
    )


if __name__ == "__main__":
    main()
