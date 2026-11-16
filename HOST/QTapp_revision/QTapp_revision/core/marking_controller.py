# -*- coding: utf-8 -*-
"""
marking_controller.py — 手动标点控制器

职责：
    - 管理手动标点模式的进入/退出
    - 接收鼠标点击坐标，维护标定点列表
    - 实时拟合圆或椭圆（硬约束，必须过所有标定点）
    - 将结果绘制到截图上并更新 UI 结果 Label

与 CalibController 的接入点（仅3处）：
    1. initialize()  — 绑定两个按钮
    2. _on_close_camera() — 关闭摄像头时重置状态
    3. eventFilter()  — 分发 KeyPress / MouseButtonPress 事件
"""

import logging
import numpy as np
import cv2
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)


class MarkingController(QtCore.QObject):
    """
    手动标点控制器。

    参数：
        ui      — MainWindow 实例（访问 UI 控件）
        show_frame_fn  — CalibController._show_frame 的引用（在主线程显示图像）
        get_last_frame_fn — 返回当前 _last_frame 的可调用对象
        get_frozen_fn / set_frozen_fn — 读写 CalibController._frozen 的可调用对象
        get_roi_selecting_fn — 返回 CalibController._roi_selecting 的可调用对象
    """

    MARKING_TOLERANCE: float = 2.0  # 像素容差，可后续改为可配置

    def __init__(self, ui, show_frame_fn, get_last_frame_fn,
                 get_frozen_fn, set_frozen_fn, get_roi_selecting_fn,
                 parent=None):
        super().__init__(parent)
        self._ui = ui
        self._show_frame = show_frame_fn
        self._get_last_frame = get_last_frame_fn
        self._get_frozen = get_frozen_fn
        self._set_frozen = set_frozen_fn
        self._get_roi_selecting = get_roi_selecting_fn

        self._marking_mode: bool = False
        self._snapshot: Optional[np.ndarray] = None
        self._points: list = []

    # ──────────────────────────────────────────
    #  对外接口（CalibController 调用）
    # ──────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._marking_mode

    def connect_buttons(self):
        """绑定 PB_shoudongbiaodian / PB_quxiaobiaodian 按钮。"""
        ui = self._ui
        if hasattr(ui, "PB_shoudongbiaodian"):
            ui.PB_shoudongbiaodian.clicked.connect(self._on_enter)
        if hasattr(ui, "PB_quxiaobiaodian"):
            ui.PB_quxiaobiaodian.clicked.connect(self._on_exit)

    def reset(self):
        """摄像头关闭时调用，清空所有状态。"""
        self._marking_mode = False
        self._snapshot = None
        self._points = []

    def handle_event(self, event) -> bool:
        """
        由 CalibController.eventFilter 调用。
        返回 True 表示事件已消费，False 表示交给后续处理。
        """
        # Delete 键：删除最后一个标定点
        if event.type() == QtCore.QEvent.KeyPress:
            if self._marking_mode and event.key() == QtCore.Qt.Key_Delete:
                if self._points:
                    self._points.pop()
                    self._redraw()
                return True
            return False

        # 鼠标点击：添加标定点（仅标点模式且非 ROI 框选时）
        if (event.type() == QtCore.QEvent.MouseButtonPress
                and self._marking_mode
                and not self._get_roi_selecting()):
            if event.button() == QtCore.Qt.LeftButton:
                ix, iy = self._to_image_coords(event.pos().x(), event.pos().y())
                self._points.append((ix, iy))
                self._redraw()
                return True

        return False

    # ──────────────────────────────────────────
    #  进入 / 退出标点模式
    # ──────────────────────────────────────────

    def _on_enter(self):
        last_frame = self._get_last_frame()
        if last_frame is None:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先开启摄像头")
            return
        if self._get_roi_selecting():
            return
        self._set_frozen(True)
        self._snapshot = last_frame.copy()
        self._points = []
        self._marking_mode = True
        label = getattr(self._ui, "labelCalibCameraView", None)
        if label is not None:
            label.setFocusPolicy(QtCore.Qt.ClickFocus)
        self._set_status("手动标点中：请点击边缘点")
        self._show_frame(self._snapshot)
        logger.info("[标点] 进入手动标点模式")

    def _on_exit(self):
        self._marking_mode = False
        self._set_frozen(False)
        self._snapshot = None
        self._points = []
        for name in ("label_calibCxValue", "label_calibCyValue", "label_calibOffsetValue"):
            w = getattr(self._ui, name, None)
            if w:
                w.setText("—")
        self._set_status("")
        logger.info("[标点] 退出手动标点模式")

    # ──────────────────────────────────────────
    #  坐标转换
    # ──────────────────────────────────────────

    def _to_image_coords(self, label_x: int, label_y: int) -> tuple:
        """Label 坐标 → 快照图像坐标（使用快照尺寸，避免 _last_frame 持续更新导致错位）。"""
        label = getattr(self._ui, "labelCalibCameraView", None)
        if label is None or self._snapshot is None:
            return (0, 0)
        fh, fw = self._snapshot.shape[:2]
        lw, lh = label.width(), label.height()
        scale = min(lw / fw, lh / fh)
        ox = (lw - int(fw * scale)) // 2
        oy = (lh - int(fh * scale)) // 2
        ix = int((label_x - ox) / scale)
        iy = int((label_y - oy) / scale)
        return (max(0, min(ix, fw - 1)), max(0, min(iy, fh - 1)))

    # ──────────────────────────────────────────
    #  重绘
    # ──────────────────────────────────────────

    def _redraw(self):
        if self._snapshot is None:
            return
        canvas = self._snapshot.copy()
        points = self._points
        n = len(points)

        fit = self._fit(points)

        if fit is not None:
            cx_i, cy_i = int(round(fit['cx'])), int(round(fit['cy']))
            if fit['type'] == 'circle':
                cv2.circle(canvas, (cx_i, cy_i), int(round(fit['radius'])), (0, 255, 0), 2)
            else:
                cv2.ellipse(canvas, (cx_i, cy_i),
                            (int(round(fit['axes'][0])), int(round(fit['axes'][1]))),
                            fit['angle'], 0, 360, (0, 255, 0), 2)
            cv2.line(canvas, (cx_i - 8, cy_i), (cx_i + 8, cy_i), (0, 255, 255), 1)
            cv2.line(canvas, (cx_i, cy_i - 8), (cx_i, cy_i + 8), (0, 255, 255), 1)

        for (px, py) in points:
            cv2.rectangle(canvas, (px - 1, py - 1), (px + 1, py + 1), (0, 0, 255), -1)

        if fit is not None:
            h, w = self._snapshot.shape[:2]
            cx_r, cy_r = int(round(fit['cx'])), int(round(fit['cy']))
            self._set_result(str(cx_r), str(cy_r), f"({cx_r - w//2}, {h//2 - cy_r})")
            self._set_status("手动标点中：请点击边缘点")
        else:
            self._set_result("—", "—", "—")
            if n < 3:
                self._set_status("点数不足：至少需要 3 个点确定圆")
            elif n == 4:
                self._set_status("当前 4 点不满足圆，请继续标第 5 点")
            else:
                self._set_status("当前点集无法确定有效圆或椭圆，请删除错误点")

        self._show_frame(canvas)

    # ──────────────────────────────────────────
    #  几何拟合（硬约束）
    # ──────────────────────────────────────────

    def _fit(self, points: list) -> Optional[dict]:
        """硬约束拟合，结果必须通过所有标定点。"""
        n = len(points)
        if n < 3:
            return None

        pts = np.array(points, dtype=float)

        def _circle_from_3(p0, p1, p2):
            ax, ay = p0; bx, by = p1; cx, cy = p2
            A = np.array([[2*(bx-ax), 2*(by-ay)],
                          [2*(cx-ax), 2*(cy-ay)]], dtype=float)
            b = np.array([bx**2+by**2-ax**2-ay**2,
                          cx**2+cy**2-ax**2-ay**2], dtype=float)
            try:
                center = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                return None
            return center, float(np.hypot(center[0]-ax, center[1]-ay))

        # 找第一个非共线三元组
        circle_result = None
        for k in range(2, n):
            res = _circle_from_3(pts[0], pts[1], pts[k])
            if res is not None:
                circle_result = res
                break

        # 验证圆
        if circle_result is not None:
            center, r = circle_result
            dists = np.hypot(pts[:, 0] - center[0], pts[:, 1] - center[1])
            if np.all(np.abs(dists - r) <= self.MARKING_TOLERANCE):
                return {'type': 'circle', 'cx': float(center[0]),
                        'cy': float(center[1]), 'radius': r}

        if n < 5:
            return None

        # 椭圆：SVD 求一般二次曲线
        x, y = pts[:, 0], pts[:, 1]
        D = np.column_stack([x*x, x*y, y*y, x, y, np.ones(n)])
        try:
            _, _, Vt = np.linalg.svd(D)
        except np.linalg.LinAlgError:
            return None
        A, B, C, Dc, E, F = Vt[-1]

        if B*B - 4*A*C >= 0:
            return None

        try:
            center = np.linalg.solve([[2*A, B], [B, 2*C]], [-Dc, -E])
        except np.linalg.LinAlgError:
            return None
        cx, cy = center

        M = np.array([[A, B/2], [B/2, C]])
        eigenvalues, eigenvectors = np.linalg.eigh(M)
        F_eff = A*cx*cx + B*cx*cy + C*cy*cy + Dc*cx + E*cy + F
        if F_eff == 0 or np.isnan(F_eff):
            return None
        axes_sq = -F_eff / eigenvalues
        if np.any(axes_sq <= 0):
            return None
        semi_axes = np.sqrt(axes_sq)
        if np.any(semi_axes < 1):
            return None

        # 验证所有点（梯度归一化像素距离）
        conic_vals = A*x*x + B*x*y + C*y*y + Dc*x + E*y + F
        grad_mag = np.hypot(2*A*x + B*y + Dc, B*x + 2*C*y + E)
        grad_mag = np.where(grad_mag < 1e-9, 1e-9, grad_mag)
        if np.any(np.abs(conic_vals) / grad_mag > self.MARKING_TOLERANCE):
            return None

        return {
            'type': 'ellipse',
            'cx': float(cx), 'cy': float(cy),
            'axes': (float(semi_axes[1]), float(semi_axes[0])),
            'angle': float(np.degrees(np.arctan2(eigenvectors[1, 1], eigenvectors[0, 1]))),
        }

    # ──────────────────────────────────────────
    #  UI 辅助
    # ──────────────────────────────────────────

    def _set_status(self, text: str):
        w = getattr(self._ui, "LB_biaodianxinxi", None)
        if w:
            w.setText(text)

    def _set_result(self, cx: str, cy: str, offset: str):
        for name, val in (("label_calibCxValue", cx),
                          ("label_calibCyValue", cy),
                          ("label_calibOffsetValue", offset)):
            w = getattr(self._ui, name, None)
            if w:
                w.setText(val)
