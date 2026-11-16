"""
圆心检测算法 - 汇报用独立文件
=====================================
本文件提取自项目 algorithms/calib_detector.py 和 core/calib_controller.py
仅用于汇报展示，不影响原有系统功能。

算法流水线：
    原始图像 → 灰度化 → 高斯模糊 → Canny边缘检测 → 形态学闭运算
              → 轮廓检测 → 面积过滤 → 轮廓平滑 → 圆心计算 → 结果可视化
"""

import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ============================================================
# 第一部分：检测参数配置
# ============================================================

@dataclass
class DetectorParams:
    """
    圆心检测算法的所有可调参数。
    每个参数都可在UI中实时调整，方便不同工况下的适配。
    """
    blur_ksize:   int   = 7      # 高斯模糊核大小（必须为奇数），越大去噪越强但边缘越模糊
    canny_lo:     int   = 50     # Canny算子低阈值，低于此值的梯度点被丢弃
    canny_hi:     int   = 150    # Canny算子高阈值，高于此值的梯度点被保留为强边缘
    morph_ksize:  int   = 21     # 形态学闭运算核大小（必须为奇数），用于连接断开的边缘轮廓
    morph_iter:   int   = 1      # 闭运算迭代次数
    min_area:     int   = 80     # 最小封闭区域面积（像素²），小于此值的区域视为噪声
    smooth_window: int  = 7      # 轮廓平滑滑动窗口大小（必须为奇数）
    roi: Optional[Tuple[int,int,int,int]] = None  # ROI区域 (x, y, w, h)，None表示全图检测


# ============================================================
# 第二部分：核心算法步骤
# ============================================================

def step1_preprocess(image: np.ndarray, params: DetectorParams):
    """
    步骤1：图像预处理
    - 灰度化：将彩色图像转为单通道灰度图
    - 高斯模糊：消除高频噪声，防止后续边缘检测误检
    """
    # 灰度化
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 高斯模糊
    blurred = cv2.GaussianBlur(gray, (params.blur_ksize, params.blur_ksize), 0)

    return gray, blurred


def step2_edge_detection(blurred: np.ndarray, params: DetectorParams):
    """
    步骤2：Canny 边缘检测
    - 利用梯度幅值找到图像中的边缘
    - canny_lo / canny_hi 两个阈值控制边缘的灵敏度
    """
    edges = cv2.Canny(blurred, params.canny_lo, params.canny_hi)
    return edges


def step3_morphology_close(edges: np.ndarray, params: DetectorParams):
    """
    步骤3：形态学闭运算
    - 闭运算 = 先膨胀后腐蚀
    - 作用：连接断裂的边缘线段，使轮廓封闭，便于后续填充
    """
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (params.morph_ksize, params.morph_ksize)
    )
    closed = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE, kernel,
        iterations=params.morph_iter
    )
    return closed


def step4_flood_fill_and_filter(closed: np.ndarray, params: DetectorParams):
    """
    步骤4：洪水填充 + 面积过滤
    - 洪水填充：从图像边界向内填充，将背景与目标区域分离
    - 面积过滤：去除面积小于 min_area 的微小轮廓（噪声）
    返回：过滤后的二值掩膜（白色区域为目标）
    """
    h, w = closed.shape

    # 创建带边框的临时图（洪水填充需要比图像大1像素的mask）
    flood_mask = np.zeros((h + 2, w + 2), np.uint8)
    filled = closed.copy()

    # 从四个角点发起洪水填充，将背景（外部区域）填充为白色
    for seed in [(0, 0), (0, h - 1), (w - 1, 0), (w - 1, h - 1)]:
        cv2.floodFill(filled, flood_mask, seed, 255)

    # 取反：背景变黑，目标区域变白
    region_mask = cv2.bitwise_not(filled)

    # ---- 面积过滤 ----
    contours, _ = cv2.findContours(
        region_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    filtered_mask = np.zeros_like(region_mask)
    for cnt in contours:
        if cv2.contourArea(cnt) >= params.min_area:
            cv2.drawContours(filtered_mask, [cnt], -1, 255, thickness=cv2.FILLED)

    return filtered_mask


def step5_smooth_contours(region_mask: np.ndarray, params: DetectorParams):
    """
    步骤5：轮廓平滑 + 圆心计算（核心步骤）

    平滑方法：滑动平均（Moving Average）
    - 对轮廓上的每个像素点坐标做卷积平均
    - 环形填充保证首尾连接处不产生突变

    圆心计算方法：图像矩（cv2.moments）
    - M["m00"] = 轮廓面积
    - cx = M["m10"] / M["m00"]   （x方向一阶矩 / 面积 = x重心）
    - cy = M["m01"] / M["m00"]   （y方向一阶矩 / 面积 = y重心）

    返回：轮廓信息列表，每项包含 cx, cy, area, w, h 等字段
    """
    contours, _ = cv2.findContours(
        region_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE  # NONE：保留所有轮廓点
    )

    results = []
    win = params.smooth_window
    pad = win // 2

    for cnt in contours:
        if len(cnt) < win:
            continue  # 点数不足，跳过

        # 提取轮廓坐标 shape=(N, 2)
        pts = cnt[:, 0, :].astype(np.float32)

        # 环形填充：将末尾 pad 个点接到开头，开头 pad 个点接到末尾
        pts_pad = np.concatenate([pts[-pad:], pts, pts[:pad]], axis=0)

        # 滑动平均卷积核
        kernel = np.ones(win) / win

        # 分别对 x、y 坐标做1D卷积
        xs = np.convolve(pts_pad[:, 0], kernel, mode="valid")
        ys = np.convolve(pts_pad[:, 1], kernel, mode="valid")

        # 重新组合为轮廓格式 shape=(N, 1, 2)
        smooth_pts = np.stack([xs, ys], axis=1).astype(np.float32)
        smooth_pts = smooth_pts.reshape((-1, 1, 2))

        # ---- 计算圆心（重心法）----
        M = cv2.moments(smooth_pts)
        if M["m00"] == 0:
            continue  # 面积为0，跳过（退化情况）

        cx = int(M["m10"] / M["m00"])   # x 坐标
        cy = int(M["m01"] / M["m00"])   # y 坐标

        # 计算外接矩形（用于估算半径）
        x, y, rw, rh = cv2.boundingRect(smooth_pts.astype(np.int32))
        area = cv2.contourArea(smooth_pts)
        peri = cv2.arcLength(smooth_pts, closed=True)

        results.append({
            "cx": cx,           # 圆心 x 坐标（像素）
            "cy": cy,           # 圆心 y 坐标（像素）
            "area": area,       # 轮廓面积（像素²）
            "w": rw,            # 外接矩形宽
            "h": rh,            # 外接矩形高
            "perimeter": peri,  # 轮廓周长
            "contour": smooth_pts,  # 平滑后的轮廓点
        })

    return results


# ============================================================
# 第三部分：完整流水线（一键调用）
# ============================================================

def run_pipeline(image: np.ndarray, params: DetectorParams = None):
    """
    完整圆心检测流水线。

    参数：
        image   - BGR 格式的输入图像（numpy数组）
        params  - 检测参数，None时使用默认值

    返回：
        overlay      - 叠加了检测结果的可视化图像
        steps        - 各中间步骤的图像字典（用于调试/展示）
        contour_info - 检测到的轮廓信息列表
    """
    if params is None:
        params = DetectorParams()

    # ---- ROI 裁剪（可选）----
    if params.roi is not None:
        rx, ry, rw, rh = params.roi
        roi_image = image[ry:ry+rh, rx:rx+rw]
    else:
        rx, ry = 0, 0
        roi_image = image

    # ---- 执行各步骤 ----
    gray, blurred  = step1_preprocess(roi_image, params)
    edges          = step2_edge_detection(blurred, params)
    closed         = step3_morphology_close(edges, params)
    filtered_mask  = step4_flood_fill_and_filter(closed, params)
    contour_info   = step5_smooth_contours(filtered_mask, params)

    # ---- ROI 坐标转换回全局坐标 ----
    for info in contour_info:
        info["cx"] += rx
        info["cy"] += ry

    # ---- 可视化：在原图上绘制结果 ----
    overlay = image.copy()
    for info in contour_info:
        cx, cy = info["cx"], info["cy"]
        r = int((info["w"] + info["h"]) / 4)   # 近似半径

        # 绘制轮廓（绿色）
        if params.roi is not None:
            contour_shifted = info["contour"].copy()
            contour_shifted[:, 0, 0] += rx
            contour_shifted[:, 0, 1] += ry
            cv2.drawContours(overlay, [contour_shifted.astype(np.int32)], -1, (0, 255, 0), 2)
        else:
            cv2.drawContours(overlay, [info["contour"].astype(np.int32)], -1, (0, 255, 0), 2)

        # 绘制圆心（红色实心点）
        cv2.circle(overlay, (cx, cy), 5, (0, 0, 255), -1)

        # 绘制近似圆（蓝色虚线圆）
        cv2.circle(overlay, (cx, cy), r, (255, 0, 0), 1)

        # 标注坐标文字
        cv2.putText(overlay, f"({cx}, {cy})", (cx + 8, cy - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    steps = {
        "1_gray":     gray,
        "2_blurred":  blurred,
        "3_edges":    edges,
        "4_closed":   closed,
        "5_filtered": filtered_mask,
    }

    return overlay, steps, contour_info


# ============================================================
# 第四部分：偏移量计算（用于电机校准）
# ============================================================

def calc_offset(cx: int, cy: int, img_w: int, img_h: int):
    """
    计算圆心相对于图像中心的偏移量。
    用于指导电机补偿运动，使目标圆心对准视场中心。

    参数：
        cx, cy   - 检测到的圆心坐标（像素）
        img_w, img_h - 图像宽高

    返回：
        dx - 水平偏移（正值表示圆心在图像中心右侧）
        dy - 垂直偏移（正值表示圆心在图像中心上方，Y轴向上为正）
    """
    img_cx = img_w // 2
    img_cy = img_h // 2
    dx = cx - img_cx      # 水平偏移（图像坐标系）
    dy = img_cy - cy      # 垂直偏移（转换为数学坐标系，Y轴向上）
    return dx, dy


# ============================================================
# 第五部分：手动三点拟合圆（备用方法）
# ============================================================

def circle_from_3_points(p0, p1, p2):
    """
    通过三个点精确确定圆（解析法）。
    适用于手动标注三个已知点的场景。

    原理：
        三点确定唯一圆，利用两条垂直平分线的交点即为圆心。
        等效于解以下线性方程组：
            2(x1-x0)*cx + 2(y1-y0)*cy = x1²+y1² - x0²-y0²
            2(x2-x0)*cx + 2(y2-y0)*cy = x2²+y2² - x0²-y0²

    返回：
        (cx, cy) - 圆心坐标
        radius   - 半径
    """
    from math import hypot

    ax, ay = float(p0[0]), float(p0[1])
    bx, by = float(p1[0]), float(p1[1])
    cx_, cy_ = float(p2[0]), float(p2[1])

    A = np.array([
        [2 * (bx - ax), 2 * (by - ay)],
        [2 * (cx_ - ax), 2 * (cy_ - ay)]
    ])
    b = np.array([
        bx**2 + by**2 - ax**2 - ay**2,
        cx_**2 + cy_**2 - ax**2 - ay**2
    ])

    try:
        center = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return None, None   # 三点共线，无法确定圆

    radius = hypot(center[0] - ax, center[1] - ay)
    return (center[0], center[1]), radius


# ============================================================
# 第六部分：演示主程序
# ============================================================

def demo_with_camera():
    """
    使用摄像头实时演示圆心检测。
    按 'q' 退出，按 's' 打印当前检测结果。
    """
    params = DetectorParams(
        blur_ksize=7,
        canny_lo=50,
        canny_hi=150,
        morph_ksize=21,
        min_area=80,
        smooth_window=7
    )

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("无法打开摄像头，请检查设备连接。")
        return

    print("摄像头已启动。按 'q' 退出，按 's' 打印圆心坐标。")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        overlay, steps, contour_info = run_pipeline(frame, params)

        # 显示结果
        cv2.imshow("圆心检测结果", overlay)
        cv2.imshow("Canny边缘", steps["3_edges"])

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            if contour_info:
                best = max(contour_info, key=lambda x: x["area"])
                cx, cy = best["cx"], best["cy"]
                r = int((best["w"] + best["h"]) / 4)
                h, w = frame.shape[:2]
                dx, dy = calc_offset(cx, cy, w, h)
                print(f"圆心坐标: ({cx}, {cy})")
                print(f"近似半径: {r} px")
                print(f"相对图像中心偏移: dx={dx}, dy={dy}")
            else:
                print("未检测到目标圆。")

    cap.release()
    cv2.destroyAllWindows()


def demo_with_image(image_path: str):
    """
    对单张图片进行圆心检测并显示结果。
    """
    image = cv2.imread(image_path)
    if image is None:
        print(f"无法读取图像：{image_path}")
        return

    params = DetectorParams()
    overlay, steps, contour_info = run_pipeline(image, params)

    print(f"共检测到 {len(contour_info)} 个目标区域")
    for i, info in enumerate(contour_info):
        cx, cy = info["cx"], info["cy"]
        r = int((info["w"] + info["h"]) / 4)
        h, w = image.shape[:2]
        dx, dy = calc_offset(cx, cy, w, h)
        print(f"  目标 {i+1}: 圆心=({cx},{cy}), 半径≈{r}px, 偏移=({dx},{dy})")

    # 显示所有中间步骤
    for name, img in steps.items():
        cv2.imshow(name, img)
    cv2.imshow("最终结果", overlay)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    # 演示1：摄像头实时检测
    demo_with_camera()

    # 演示2：单张图片检测（取消注释并替换路径）
    # demo_with_image("test_image.jpg")
