# -*- coding: utf-8 -*-
"""
calib_controller.py  —  校准页面控制器

职责：
    - 管理摄像头的开启 / 关闭（QThread 实时预览，不阻塞 UI）
    - 读取 UI SpinBox 参数，构造 DetectorParams
    - 调用 calib_detector.run_pipeline() 执行圆心检测
    - 把结果图显示到 labelCalibCameraView
    - 把圆心坐标 / 偏移量写回 UI 的结果 Label
    - 「过程图像」按钮弹出独立小窗口，逐步展示检测中间图

使用方式（在 main_window.py 的 __init__ 里）：
    from core.calib_controller import CalibController
    self.calib_controller = CalibController(self, self)
    self.calib_controller.initialize()
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
import numpy as np
import cv2
from typing import Optional, Dict

from PyQt5 import QtCore, QtGui, QtWidgets

from algorithms.calib_detector import DetectorParams, locate_roi, run_pipeline, sample_hsv_color
from core.marking_controller import MarkingController
# [电机控制] 引入 MotorController
from core.motor_controller import MotorController

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  摄像头预览线程
# ──────────────────────────────────────────────

class _CameraWorker(QtCore.QThread):
    """
    后台线程：持续从摄像头读帧，通过信号发送给主线程显示。
    不做任何图像处理，只负责"取帧 → 发信号"。
    """

    frame_ready = QtCore.pyqtSignal(np.ndarray)   # 每帧发出原始 BGR 图像
    error       = QtCore.pyqtSignal(str)           # 打开失败时发出错误信息

    def __init__(self, cam_index: int, parent=None):
        super().__init__(parent)
        self._cam_index = cam_index
        self._running   = False
        self._cap       = None

    def run(self):
        self._cap = cv2.VideoCapture(self._cam_index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            self.error.emit(f"无法打开摄像头 {self._cam_index}")
            self._cap = None
            return

        self._running = True
        while self._running:
            ret, frame = self._cap.read()
            if ret and frame is not None:
                self.frame_ready.emit(frame.copy())
            self.msleep(33)   # ~30 fps

        self._cap.release()
        self._cap = None

    def stop(self):
        self._running = False
        self.wait(2000)


# [电机控制] 电机移动后台线程
class _MotorMoveWorker(QtCore.QThread):
    finished_signal = QtCore.pyqtSignal(bool, str)

    def __init__(self, controller, slave_id: int, displacement: int, timeout: float = 10.0, parent=None):
        super().__init__(parent)
        self._controller  = controller
        self._slave_id    = slave_id
        self._displacement = displacement
        self._timeout     = timeout

    def run(self):
        ok, err = self._controller.move_motor_and_wait(
            slave_id=self._slave_id,
            displacement=self._displacement,
            timeout=self._timeout,
        )
        self.finished_signal.emit(ok, err)


class _RoiStabilizer:
    def __init__(self, change_threshold: int = 8, lost_hold_frames: int = 8):
        self.change_threshold = int(change_threshold)
        self.lost_hold_frames = int(lost_hold_frames)
        self._stable_roi: Optional[tuple[int, int, int, int]] = None
        self._lost_frames = 0

    def reset(self):
        self._stable_roi = None
        self._lost_frames = 0

    def update(self, roi: Optional[tuple]) -> Optional[tuple[int, int, int, int]]:
        if roi is None:
            if self._stable_roi is None:
                return None
            self._lost_frames += 1
            if self._lost_frames <= self.lost_hold_frames:
                return self._stable_roi
            self.reset()
            return None

        next_roi = tuple(int(v) for v in roi)
        self._lost_frames = 0
        if self._stable_roi is None:
            self._stable_roi = next_roi
            return self._stable_roi

        deltas = [abs(a - b) for a, b in zip(next_roi, self._stable_roi)]
        if max(deltas) >= self.change_threshold:
            self._stable_roi = next_roi
        return self._stable_roi


# ──────────────────────────────────────────────
#  过程图像弹窗
# ──────────────────────────────────────────────

class _DetectWorker(QtCore.QThread):
    finished_signal = QtCore.pyqtSignal(object, object, object, object, str)

    def __init__(self, frame: np.ndarray, params: DetectorParams, parent=None):
        super().__init__(parent)
        self._frame = frame.copy()
        self._params = params

    def run(self):
        try:
            overlay, steps, contour_info = run_pipeline(self._frame, self._params)
        except Exception as exc:
            self.finished_signal.emit(self._frame, None, None, None, str(exc))
            return
        self.finished_signal.emit(self._frame, overlay, steps, contour_info, "")


class _StepViewerDialog(QtWidgets.QDialog):
    """
    显示圆心检测各中间步骤图的独立弹窗。
    - 顶部 QComboBox 切换步骤
    - 中间 QLabel 显示图像
    - 窗口可自由移动、关闭，关闭后不销毁（hide），下次直接 show()
    """

    # 步骤显示顺序
    STEP_ORDER = [
        "胶带颜色Mask",
        "颜色标记连通块",
        "胶带范围连通",
        "ROI原图",
        "灰度图",
        "高斯模糊",
        "Canny边缘",
        "屏蔽Mask(ROI)",
        "屏蔽后Canny",
        "清理后Canny",
        "自动内部点",
        "粗径向点叠加",
        "亚像素点叠加",
        "RANSAC投票内点",
        "最终拟合结果",
    ]

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("检测过程图像")
        self.setMinimumSize(520, 440)
        self.resize(640, 520)
        self.setWindowFlags(
            QtCore.Qt.Window |
            QtCore.Qt.WindowCloseButtonHint |
            QtCore.Qt.WindowMinimizeButtonHint
        )

        self._steps: Dict[str, np.ndarray] = {}
        self._current_img_size: Optional[tuple[int, int]] = None

        # ── 布局 ──
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 顶栏：步骤选择 + 标签
        top = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel("步骤：")
        lbl.setFixedWidth(40)
        self._combo = QtWidgets.QComboBox()
        self._combo.addItems(self.STEP_ORDER)
        self._combo.setFixedHeight(28)
        top.addWidget(lbl)
        top.addWidget(self._combo, 1)
        root.addLayout(top)

        # 图像显示区
        self._img_label = QtWidgets.QLabel()
        self._img_label.setAlignment(QtCore.Qt.AlignCenter)
        self._img_label.setMouseTracking(True)
        self._img_label.installEventFilter(self)
        self._img_label.setStyleSheet(
            "background:#111; border:1px solid #444; border-radius:6px;"
        )
        self._img_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        self._img_label.setText("（请先执行圆心检测）")
        self._img_label.setStyleSheet(
            "background:#111; border:1px solid #444; border-radius:6px;"
            "color:#00ff66; font-family:Consolas;"
        )
        root.addWidget(self._img_label, 1)

        # 步骤名称说明
        self._desc_label = QtWidgets.QLabel("")
        self._desc_label.setAlignment(QtCore.Qt.AlignCenter)
        self._desc_label.setStyleSheet("color:#aaa; font-size:9pt;")
        root.addWidget(self._desc_label)

        # 信号
        self._combo.currentTextChanged.connect(self._refresh_image)

    # 步骤说明文字
    _STEP_DESC = {
        "胶带颜色Mask": "按当前 HSV 胶带颜色分割，用于自动定位 ROI",
        "颜色标记连通块": "四个颜色标记的连通块结果，用于确定 ROI 外框",
        "胶带范围连通": "胶带区域连接后的备选 ROI 结果",
        "ROI原图": "自动或手动 ROI 内的原始图像",
        "灰度图": "ROI 图像转灰度，后续边缘检测只处理 ROI",
        "高斯模糊": "轻微模糊降低噪点，避免 Canny 产生碎边",
        "Canny边缘": "原始 Canny 边缘，还未删除 Mask 屏蔽区域",
        "屏蔽Mask(ROI)": "当前涂抹 Mask 在 ROI 内的区域",
        "屏蔽后Canny": "删除 Mask 覆盖区域后的 Canny 边缘",
        "清理后Canny": "继续过滤小面积杂边后的边缘",
        "自动内部点": "根据清理后的边缘 bbox 得到的径向扫描起点",
        "粗径向点叠加": "从内部点 360 度向外扫描得到的第一接触点",
        "亚像素点叠加": "沿径向轻微修正后的采样点",
        "RANSAC投票内点": "绿色为支持最终椭圆的准确点，红色为误差较大的点",
        "最终拟合结果": "最终回填到主界面的圆心/椭圆拟合结果",
    }

    def update_steps(self, steps: Dict[str, np.ndarray]):
        """检测完成后调用，传入新的步骤图字典并刷新显示。"""
        self._steps = steps
        current = self._combo.currentText()
        self._combo.blockSignals(True)
        self._combo.clear()
        ordered = [name for name in self.STEP_ORDER if name in steps]
        ordered.extend(name for name in steps.keys() if name not in ordered)
        self._combo.addItems(ordered)
        if current in steps:
            self._combo.setCurrentText(current)
        self._combo.blockSignals(False)
        self._refresh_image(self._combo.currentText())

    def _refresh_image(self, step_name: str):
        """根据当前选中的步骤名刷新图像显示。"""
        self._desc_label.setText(self._STEP_DESC.get(step_name, ""))

        img = self._steps.get(step_name)
        if img is None:
            self._current_img_size = None
            self._img_label.setText("（暂无图像，请先执行圆心检测）")
            return
        self._current_img_size = (img.shape[1], img.shape[0])

        # 灰度图转 RGB 显示
        if len(img.shape) == 2:
            img_rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        else:
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        h, w, ch = img_rgb.shape
        qimg = QtGui.QImage(img_rgb.data, w, h, ch * w,
                            QtGui.QImage.Format_RGB888).copy()
        pixmap = QtGui.QPixmap.fromImage(qimg)

        # 按标签大小等比缩放
        lw = self._img_label.width() - 4
        lh = self._img_label.height() - 4
        if lw > 0 and lh > 0:
            pixmap = pixmap.scaled(
                lw, lh,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        self._img_label.setPixmap(pixmap)

    def eventFilter(self, obj, event):
        if obj is self._img_label:
            if event.type() == QtCore.QEvent.MouseMove and self._current_img_size is not None and self._img_label.pixmap() is not None:
                pixmap = self._img_label.pixmap()
                img_w, img_h = self._current_img_size
                px_w = pixmap.width()
                px_h = pixmap.height()
                off_x = (self._img_label.width() - px_w) // 2
                off_y = (self._img_label.height() - px_h) // 2
                lx = event.pos().x() - off_x
                ly = event.pos().y() - off_y
                if 0 <= lx < px_w and 0 <= ly < px_h:
                    x = int(lx * img_w / max(1, px_w))
                    y = int(ly * img_h / max(1, px_h))
                    base = self._STEP_DESC.get(self._combo.currentText(), "")
                    self._desc_label.setText(f"{base} | 鼠标: ({x}, {y})")
                    return False
            if event.type() == QtCore.QEvent.Leave:
                self._desc_label.setText(self._STEP_DESC.get(self._combo.currentText(), ""))
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        """关闭时隐藏而不销毁，保留步骤数据。"""
        event.ignore()
        self.hide()

    def resizeEvent(self, event):
        """窗口缩放时刷新图像以适配新尺寸。"""
        super().resizeEvent(event)
        self._refresh_image(self._combo.currentText())


# ──────────────────────────────────────────────
#  校准控制器
# ──────────────────────────────────────────────

class CalibController(QtCore.QObject):
    """
    校准页面的业务控制器。

    参数：
        ui      —— MainWindow 实例（用于访问所有 UI 控件）
        parent  —— QObject 父对象
    """

    def __init__(self, ui, parent=None, motor: Optional[MotorController] = None):
        super().__init__(parent)
        self._ui             = ui
        self._worker: Optional[_CameraWorker] = None
        self._last_frame: Optional[np.ndarray] = None
        self._previewing     = False
        self._frozen         = False
        self._last_steps: Dict[str, np.ndarray] = {}
        self._step_viewer: Optional[_StepViewerDialog] = None
        self._detect_worker: Optional[_DetectWorker] = None
        self._tape_hsv = (102, 183, 249)
        self._waiting_for_tape_pick = False
        self._mask_editing = False
        self._probe_mask: Optional[np.ndarray] = None
        self._mask_brush_size = 3
        self._mask_path = self._default_mask_path()
        self._display_roi: Optional[tuple] = None
        self._roi_stabilizer = _RoiStabilizer(change_threshold=8, lost_hold_frames=8)

        # 初始化电机worker，避免hasattr检查的竞态条件
        self._motor_worker_x: Optional[_MotorMoveWorker] = None
        self._motor_worker_y: Optional[_MotorMoveWorker] = None
        self._motor_worker_z: Optional[_MotorMoveWorker] = None

        # [电机控制] 使用外部传入的电机控制器（由 MainWindow 统一管理）
        self._motor: Optional[MotorController] = motor

        # [ROI功能] ROI选择状态
        self._roi: Optional[tuple] = None  # (x, y, w, h)
        self._roi_selecting: bool = False  # 是否正在框选ROI
        self._roi_start_point: Optional[tuple] = None  # ROI起点
        self._frozen_before_roi: bool = False  # ROI选择前的冻结状态
        self._last_preview_time: float = 0  # 上次预览时间（用于节流）

        # [手动标点] 委托给独立控制器
        self._marking = MarkingController(
            ui=ui,
            show_frame_fn=self._show_frame,
            get_last_frame_fn=lambda: self._last_frame,
            get_frozen_fn=lambda: self._frozen,
            set_frozen_fn=lambda v: setattr(self, '_frozen', v),
            get_roi_selecting_fn=lambda: self._roi_selecting,
        )

    # ──────────────────────────────────────────
    #  对外接口
    # ──────────────────────────────────────────

    def initialize(self):
        """连接校准页面所有按钮的信号。"""
        ui = self._ui

        if hasattr(ui, "btnCalibOpenCamera"):
            ui.btnCalibOpenCamera.clicked.connect(self._on_open_camera)

        if hasattr(ui, "btnCalibCloseCamera"):
            ui.btnCalibCloseCamera.clicked.connect(self._on_close_camera)

        if hasattr(ui, "btnCalibDetect"):
            ui.btnCalibDetect.clicked.connect(self._on_detect)

        if hasattr(ui, "btnCalibCapture"):
            # UI 上文字为"继续预览"：恢复摄像头实时预览
            ui.btnCalibCapture.clicked.connect(self._on_resume_preview)

        if hasattr(ui, "btnCalibResetParams"):
            ui.btnCalibResetParams.clicked.connect(self._on_reset_params)

        if hasattr(ui, "btnmove"):
            ui.btnmove.clicked.connect(self._on_auto_offset)

        # [电机控制] 定位按钮：运行固定 X/Y 轴位移
        if hasattr(ui, "btnCalibAutoAlign"):
            ui.btnCalibAutoAlign.clicked.connect(self._on_auto_align)

        # 连接 UI 中已有的「过程图像」按钮，初始禁用
        if hasattr(ui, "btnguocheng"):
            ui.btnguocheng.setEnabled(False)
            ui.btnguocheng.setToolTip("查看圆心检测各中间步骤图像（需先执行一次检测）")
            ui.btnguocheng.clicked.connect(self._on_show_step_viewer)

        # 创建弹窗（不显示）
        self._step_viewer = _StepViewerDialog(ui if isinstance(ui, QtWidgets.QWidget) else None)

        # [电机控制] 连接 X/Y 轴移动按钮
        if hasattr(ui, "btnCalibMoveX"):
            ui.btnCalibMoveX.clicked.connect(self._on_move_x)
        if hasattr(ui, "btnCalibMoveY"):
            ui.btnCalibMoveY.clicked.connect(self._on_move_y)

        # [ROI功能] 连接ROI按钮
        if hasattr(ui, "btnCalibSelectROI"):
            ui.btnCalibSelectROI.clicked.connect(self._on_select_roi)
        if hasattr(ui, "btnCalibClearROI"):
            ui.btnCalibClearROI.clicked.connect(self._on_clear_roi)

        # [手动标点] 委托给 MarkingController 绑定按钮
        self._configure_tape_detector_controls()
        self._load_probe_mask()

        # [ROI功能] 为摄像头预览Label安装事件过滤器，用于ROI框选
        if hasattr(ui, "labelCalibCameraView"):
            ui.labelCalibCameraView.installEventFilter(self)
            ui.labelCalibCameraView.setMouseTracking(True)

        logger.info("[校准] 控制器初始化完成")

    def _configure_tape_detector_controls(self):
        ui = self._ui

        if hasattr(ui, "PB_shoudongbiaodian"):
            ui.PB_shoudongbiaodian.clicked.connect(self._on_pick_tape_color)
        if hasattr(ui, "PB_quxiaobiaodian"):
            ui.PB_quxiaobiaodian.clicked.connect(self._on_cancel_tape_pick)

        if hasattr(ui, "btnCalibMaskEdit"):
            ui.btnCalibMaskEdit.clicked.connect(self._on_start_mask_edit)
        if hasattr(ui, "btnCalibMaskSave"):
            ui.btnCalibMaskSave.clicked.connect(self._on_finish_mask_edit)
        if hasattr(ui, "btnCalibMaskClear"):
            ui.btnCalibMaskClear.clicked.connect(self._on_clear_probe_mask)
        if hasattr(ui, "spinBoxCalibMaskBrush"):
            ui.spinBoxCalibMaskBrush.setValue(self._mask_brush_size)
            ui.spinBoxCalibMaskBrush.valueChanged.connect(lambda v: setattr(self, "_mask_brush_size", int(v)))
        self._sync_mask_buttons()
        self._set_tape_status(f"HSV: {self._tape_hsv}")

    def _default_mask_path(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "calib_probe_mask.png"
        return Path(__file__).resolve().parents[1] / "calib_probe_mask.png"

    def _sync_mask_buttons(self):
        ui = self._ui
        edit_btn = getattr(ui, "btnCalibMaskEdit", None)
        save_btn = getattr(ui, "btnCalibMaskSave", None)
        clear_btn = getattr(ui, "btnCalibMaskClear", None)
        if edit_btn is not None:
            edit_btn.setEnabled(self._previewing and not self._mask_editing)
        if save_btn is not None:
            save_btn.setEnabled(self._previewing and self._mask_editing)
        if clear_btn is not None:
            clear_btn.setEnabled(self._probe_mask is not None or self._mask_editing)

    def _set_mask_status(self, text: str):
        label = getattr(self._ui, "labelCalibMaskStatus", None)
        if label is not None:
            label.setText(text)

    def _set_calib_text(self, name: str, text: str):
        widget = getattr(self._ui, name, None)
        if widget is not None:
            widget.setText(text)

    def _set_tape_status(self, text: str):
        self._set_calib_text("LB_biaodianxinxi", text)

    def _ensure_probe_mask_size(self):
        if self._last_frame is None:
            return
        shape = self._last_frame.shape[:2]
        if self._probe_mask is None or self._probe_mask.shape[:2] != shape:
            self._probe_mask = np.zeros(shape, dtype=np.uint8)

    def _load_probe_mask(self):
        if self._mask_path.exists():
            mask = cv2.imread(str(self._mask_path), cv2.IMREAD_GRAYSCALE)
            if mask is not None:
                self._probe_mask = mask
                self._set_mask_status(f"Mask: 已加载 {self._mask_path.name}")
                self._sync_mask_buttons()
                return
        self._set_mask_status("Mask: 未设置")
        self._sync_mask_buttons()

    def _save_probe_mask(self):
        if self._probe_mask is None:
            return
        self._mask_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(self._mask_path), self._probe_mask)
        self._set_mask_status(f"Mask: 已保存 {self._mask_path.name}")

    def _on_start_mask_edit(self):
        if not self._previewing or self._last_frame is None:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先打开摄像头并等待画面")
            return
        self._ensure_probe_mask_size()
        self._mask_editing = True
        self._waiting_for_tape_pick = False
        self._roi_selecting = False
        self._frozen = False
        self._display_roi = None
        self._show_mask_overlay()
        self._sync_mask_buttons()

    def _on_finish_mask_edit(self):
        self._mask_editing = False
        self._save_probe_mask()
        self._sync_mask_buttons()
        if self._last_frame is not None:
            self._show_preview_frame(self._last_frame)

    def _on_clear_probe_mask(self):
        if self._last_frame is not None:
            self._probe_mask = np.zeros(self._last_frame.shape[:2], dtype=np.uint8)
        else:
            self._probe_mask = None
        self._mask_editing = False
        if self._mask_path.exists():
            self._mask_path.unlink()
        self._set_mask_status("Mask: 未设置")
        self._sync_mask_buttons()
        if self._last_frame is not None:
            self._show_preview_frame(self._last_frame)

    def _paint_probe_mask(self, label_x: int, label_y: int, buttons: int):
        if not self._mask_editing or self._last_frame is None:
            return
        self._ensure_probe_mask_size()
        if self._probe_mask is None:
            return
        x, y = self._label_to_image_coords(label_x, label_y, use_display_roi=False)
        value = 255 if (buttons & int(QtCore.Qt.LeftButton)) else 0
        if not (buttons & int(QtCore.Qt.LeftButton)) and not (buttons & int(QtCore.Qt.RightButton)):
            return
        cv2.circle(self._probe_mask, (int(x), int(y)), int(self._mask_brush_size), value, -1)
        self._show_mask_overlay()

    def _show_mask_overlay(self):
        if self._last_frame is None:
            return
        frame = self._last_frame.copy()
        if self._probe_mask is not None and self._probe_mask.shape[:2] == frame.shape[:2]:
            red = np.zeros_like(frame)
            red[:, :] = (0, 0, 255)
            mask = self._probe_mask > 0
            frame[mask] = cv2.addWeighted(frame, 0.35, red, 0.65, 0)[mask]
        self._display_roi = None
        self._show_frame(frame)

    def _on_pick_tape_color(self):
        if not self._previewing or self._last_frame is None:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先打开摄像头并等待画面")
            return
        self._waiting_for_tape_pick = True
        self._roi_selecting = False
        self._set_tape_status("HSV: 点击画面胶带取色")

    def _on_cancel_tape_pick(self):
        self._waiting_for_tape_pick = False
        self._set_tape_status(f"HSV: {self._tape_hsv}")

    def cleanup(self):
        """窗口关闭时调用，释放摄像头资源。"""
        self._stop_camera()
        if self._detect_worker is not None and self._detect_worker.isRunning():
            self._detect_worker.wait(3000)
        if self._step_viewer is not None:
            self._step_viewer.destroy()

    def _on_show_step_viewer(self):
        """弹出（或切换到前台）过程图像窗口。"""
        if self._step_viewer is None:
            return
        if self._last_steps:
            self._step_viewer.update_steps(self._last_steps)
        self._step_viewer.show()
        self._step_viewer.raise_()
        self._step_viewer.activateWindow()

    def _on_save_debug_images(self):
        if not self._last_steps:
            QtWidgets.QMessageBox.information(self._ui, "提示", "请先执行一次圆心检测")
            return
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        out_dir = base / "calib_debug_output" / datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        for idx, (name, img) in enumerate(self._last_steps.items(), start=1):
            safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_") or f"step_{idx}"
            cv2.imwrite(str(out_dir / f"{idx:02d}_{safe}.png"), img)
        lines = [
            getattr(self._ui, "LB_biaodianxinxi", QtWidgets.QLabel("")).text(),
            getattr(self._ui, "labelCalibSeedInfo", QtWidgets.QLabel("")).text(),
            getattr(self._ui, "labelCalibQualityInfo", QtWidgets.QLabel("")).text(),
            getattr(self._ui, "labelCalibEdgeInfo", QtWidgets.QLabel("")).text(),
        ]
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        QtWidgets.QMessageBox.information(self._ui, "保存完成", f"调试图已保存到:\n{out_dir}")

    # ──────────────────────────────────────────
    #  摄像头控制
    # ──────────────────────────────────────────

    def _on_open_camera(self):
        if self._previewing:
            return
        cam_index = self._read_cam_index()
        self._start_camera(cam_index)

    def _on_close_camera(self):
        self._stop_camera()
        self._frozen = False        # 解除冻结，避免下次开启摄像头时画面不更新
        self._last_frame = None     # 清空缓存帧，防止摄像头关闭后仍能检测旧帧
        self._display_roi = None
        self._roi_stabilizer.reset()
        self._marking.reset()
        self._clear_preview()

    def _start_camera(self, cam_index: int):
        self._stop_camera()
        self._frozen = False        # 开启摄像头时解除冻结，确保新帧能正常显示
        self._last_frame = None     # 清空旧帧，避免混用上次的缓存
        self._display_roi = None
        self._roi_stabilizer.reset()
        self._worker = _CameraWorker(cam_index)
        self._worker.frame_ready.connect(self._on_frame_ready)
        self._worker.error.connect(self._on_camera_error)
        self._worker.start()
        self._previewing = True
        self._sync_mask_buttons()
        logger.info(f"[校准] 摄像头 {cam_index} 已开启")

    def _stop_camera(self):
        if self._worker is not None:
            self._worker.stop()
            if self._worker.isRunning():
                logger.warning("[校准] 摄像头线程未在2秒内退出，强制终止")
                # 先手动释放摄像头，再terminate，避免资源泄漏
                if self._worker._cap is not None:
                    self._worker._cap.release()
                    self._worker._cap = None
                self._worker.terminate()
                self._worker.wait(1000)
            self._worker = None
        self._previewing = False
        self._sync_mask_buttons()

    # ──────────────────────────────────────────
    #  帧接收 & 显示
    # ──────────────────────────────────────────

    @QtCore.pyqtSlot(np.ndarray)
    def _on_frame_ready(self, frame: np.ndarray):
        """每帧回调：缓存帧 + 显示到预览 Label。"""
        self._last_frame = frame
        if self._mask_editing:
            self._show_mask_overlay()
            return
        # 冻结期间或正在框选ROI期间不刷新预览
        if not self._frozen and not self._roi_selecting:
            self._show_preview_frame(frame)

    def _show_frame(self, frame: np.ndarray):
        """把 BGR numpy 图像显示到 labelCalibCameraView。"""
        label = getattr(self._ui, "labelCalibCameraView", None)
        if label is None:
            return
        pixmap = self._frame_to_pixmap(frame, label.width(), label.height())
        label.setPixmap(pixmap)

    def _show_preview_frame(self, frame: np.ndarray, roi: Optional[tuple] = None):
        """Show the full camera frame with a stabilized ROI overlay."""
        if frame is None:
            return
        detected_roi = roi if roi is not None else self._locate_display_roi(frame)
        if roi is not None:
            self._roi_stabilizer.reset()
        display_roi = self._roi_stabilizer.update(detected_roi)
        self._display_roi = display_roi
        self._show_frame(self._draw_preview_roi_overlay(frame, display_roi))

    @staticmethod
    def _draw_preview_roi_overlay(frame: np.ndarray, roi: Optional[tuple]) -> np.ndarray:
        preview = frame.copy()
        if roi is None:
            return preview
        x, y, w, h = (int(v) for v in roi)
        if w <= 0 or h <= 0:
            return preview
        x2 = min(preview.shape[1] - 1, x + w)
        y2 = min(preview.shape[0] - 1, y + h)
        x = max(0, min(x, preview.shape[1] - 1))
        y = max(0, min(y, preview.shape[0] - 1))
        cv2.rectangle(preview, (x, y), (x2, y2), (165, 185, 185), 1, lineType=cv2.LINE_AA)
        return preview

    def _locate_display_roi(self, frame: np.ndarray) -> Optional[tuple]:
        params = self._read_params()
        params.roi = self._roi
        roi, _ = locate_roi(frame, params)
        return roi

    def _current_detection_roi(self) -> Optional[tuple]:
        return self._roi if self._roi is not None else self._display_roi

    @staticmethod
    def _frame_to_pixmap(frame: np.ndarray, max_w: int, max_h: int) -> QtGui.QPixmap:
        """BGR numpy → 填满 label 的 QPixmap（无黑边拉伸）。"""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QtGui.QImage(rgb.data, w, h, ch * w, QtGui.QImage.Format_RGB888).copy()
        pixmap = QtGui.QPixmap.fromImage(qimg)
        if max_w > 0 and max_h > 0:
            pixmap = pixmap.scaled(
                max_w, max_h,
                QtCore.Qt.KeepAspectRatio,        # 等比缩放，保持圆形不变形
                QtCore.Qt.SmoothTransformation,
            )
        return pixmap

    def _clear_preview(self):
        label = getattr(self._ui, "labelCalibCameraView", None)
        if label:
            label.clear()
            label.setAlignment(QtCore.Qt.AlignCenter)   # 确保文字居中显示
            label.setText("摄像头未开启")

    # ──────────────────────────────────────────
    #  圆心检测
    # ──────────────────────────────────────────

    def _on_detect(self):
        """冻结当前帧并执行圆心检测，结果持久显示在预览区和结果 Label。"""
        # 摄像头未开启时不允许检测
        if not self._previewing:
            QtWidgets.QMessageBox.warning(
                self._ui, "提示", "请先开启摄像头"
            )
            return
        if self._last_frame is None:
            QtWidgets.QMessageBox.warning(
                self._ui, "提示", "摄像头已开启，请稍候，尚未收到画面"
            )
            return
        # 冻结画面：后续新帧不再刷新预览
        self._frozen = True
        self._run_detect(self._last_frame)

    def _run_detect(self, frame: np.ndarray):
        """执行检测流水线并更新 UI。"""
        params = self._read_params()
        if self._detect_worker is not None and self._detect_worker.isRunning():
            return
        btn_detect = getattr(self._ui, "btnCalibDetect", None)
        if btn_detect is not None:
            btn_detect.setEnabled(False)
        self._detect_worker = _DetectWorker(frame, params)
        self._detect_worker.finished_signal.connect(self._on_detect_finished)
        self._detect_worker.finished.connect(self._detect_worker.deleteLater)
        self._detect_worker.start()

    # ──────────────────────────────────────────
    #  结果写回 UI
    # ──────────────────────────────────────────

    def _on_detect_finished(self, frame, overlay, steps, contour_info, error: str):
        btn = getattr(self._ui, "btnCalibDetect", None)
        if btn is not None:
            btn.setEnabled(True)
        self._detect_worker = None

        if error:
            logger.error(f"[calib] detect failed: {error}")
            self._frozen = False
            QtWidgets.QMessageBox.critical(self._ui, "检测失败", error)
            return

        self._last_steps = steps or {}
        process_btn = getattr(self._ui, "btnguocheng", None)
        if process_btn is not None:
            process_btn.setEnabled(bool(self._last_steps))
        if self._step_viewer is not None and self._step_viewer.isVisible():
            self._step_viewer.update_steps(self._last_steps)
        if overlay is not None:
            self._show_frame(self._draw_preview_roi_overlay(overlay, self._display_roi))
        self._update_result_labels(frame, contour_info or [])
        self._update_debug_labels(contour_info or [])
        logger.info(f"[calib] detect finished, results={len(contour_info or [])}")

    def _update_debug_labels(self, contour_info: list):
        if not contour_info:
            self._set_calib_text("labelCalibSeedInfo", "内部点: -")
            self._set_calib_text("labelCalibQualityInfo", "质量: 未找到可靠椭圆")
            self._set_calib_text("labelCalibEdgeInfo", "边缘: -")
            self._set_tape_status(f"HSV: {self._tape_hsv}")
            return
        best = max(contour_info, key=lambda x: x["area"])
        sx = best.get("seed_x")
        sy = best.get("seed_y")
        if sx is not None and sy is not None:
            self._set_calib_text("labelCalibSeedInfo", f"内部点: 图像({sx:.1f}, {sy:.1f})")
        mean_error = float(best.get("mean_error", 0.0))
        coverage = float(best.get("coverage", 0.0))
        points = int(best.get("inlier_points", 0))
        refit = "是" if best.get("final_refit") else "否"
        self._set_calib_text("labelCalibQualityInfo", f"质量: 误差={mean_error:.2f}, 覆盖={coverage:.2f}, 内点={points}, 精拟合={refit}")
        radial = int(best.get("radial_points", 0))
        subpixel = int(best.get("subpixel_points", 0))
        shift_mean = float(best.get("subpixel_shift_mean", 0.0))
        shift_max = float(best.get("subpixel_shift_max", 0.0))
        self._set_calib_text("labelCalibEdgeInfo", f"边缘: 粗点={radial}, 亚像素点={subpixel}, 修正={shift_mean:.2f}/{shift_max:.2f}px")
        self._set_tape_status(f"HSV: {self._tape_hsv}  ROI: {best.get('roi_source', '-')}")

    def _update_result_labels(
        self,
        frame: np.ndarray,
        contour_info: list,
    ):
        """
        把检测结果写入 groupBox_calibDetectResult 里的 Label。
        取面积最大的轮廓作为主目标。
        """
        ui = self._ui

        # 图像中心
        img_cx = frame.shape[1] // 2
        img_cy = frame.shape[0] // 2

        if contour_info:
            # 取面积最大的轮廓
            best = max(contour_info, key=lambda x: x["area"])
            cx   = best["cx"] if best["cx"] is not None else 0
            cy   = best["cy"] if best["cy"] is not None else 0
            r    = int((best["w"] + best["h"]) / 4)   # 近似半径
            dx   = cx - img_cx
            dy   = img_cy - cy
        else:
            cx = cy = r = dx = dy = 0

        def _set(name, text):
            w = getattr(ui, name, None)
            if w:
                w.setText(text)

        _set("label_calibCxValue",     str(cx))
        _set("label_calibCyValue",     str(cy))
        _set("label_calibRadiusValue", str(r))
        _set("label_calibOffsetValue", f"({dx}, {dy})")

    def _clear_detection_result_labels(self):
        """Clear stale circle result values after the target has moved."""
        def _set(name, text):
            w = getattr(self._ui, name, None)
            if w:
                w.setText(text)

        _set("label_calibCxValue", "0")
        _set("label_calibCyValue", "0")
        _set("label_calibRadiusValue", "0")
        _set("label_calibOffsetValue", "—")
        self._set_calib_text("labelCalibSeedInfo", "内部点: -")
        self._set_calib_text("labelCalibQualityInfo", "质量: 已移动，请重新检测")
        self._set_calib_text("labelCalibEdgeInfo", "边缘: -")

    def _resume_preview_after_motion(self):
        """After motor motion, old overlay/ROI result is stale; resume live ROI preview."""
        self._frozen = False
        self._last_steps = {}
        process_btn = getattr(self._ui, "btnguocheng", None)
        if process_btn is not None:
            process_btn.setEnabled(False)
        self._clear_detection_result_labels()
        if self._last_frame is not None:
            self._show_preview_frame(self._last_frame)

    # ──────────────────────────────────────────
    #  参数读取 / 重置
    # ──────────────────────────────────────────

    def _read_params(self) -> DetectorParams:
        """从 UI SpinBox 读取当前参数。"""
        ui = self._ui

        def _val(name, default):
            w = getattr(ui, name, None)
            return w.value() if w is not None else default

        return DetectorParams(
            blur_ksize    = _val("spinBox_blurKsize",    7),
            canny_lo      = _val("spinBox_cannyLo",      50),
            canny_hi      = _val("spinBox_cannyHi",      150),
            min_area      = _val("spinBox_minArea",      80),
            roi_inner_margin       = _val("spinBox_morphKsize",   18),
            ransac_inlier_tolerance = float(_val("spinBox_smoothWindow", 3)),
            max_mean_error          = float(_val("spinBox_morphNumber", 4)),
            tape_hsv      = self._tape_hsv,
            ignore_mask   = self._probe_mask.copy() if self._probe_mask is not None else None,
            roi           = self._current_detection_roi(),
        )

    def _on_reset_params(self):
        """恢复所有 SpinBox 为默认值。"""
        defaults = DetectorParams()
        ui = self._ui

        def _set(name, val):
            w = getattr(ui, name, None)
            if w:
                w.setValue(val)

        _set("spinBox_blurKsize",    defaults.blur_ksize)
        _set("spinBox_cannyLo",      defaults.canny_lo)
        _set("spinBox_cannyHi",      defaults.canny_hi)
        _set("spinBox_morphKsize",   defaults.roi_inner_margin)
        _set("spinBox_morphNumber",  int(defaults.max_mean_error))
        _set("spinBox_minArea",      defaults.min_area)
        _set("spinBox_smoothWindow", int(defaults.ransac_inlier_tolerance))

        logger.info("[校准] 参数已恢复默认值")

    # ──────────────────────────────────────────
    #  自动偏移校准与定位
    # ──────────────────────────────────────────

    def _on_auto_offset(self):
        """自动偏移校准：使用上次检测结果的偏移量调整 X/Y 轴。"""
        if not self._motor or not self._motor.connected:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先连接电机串口")
            return

        # 读取 UI 上已显示的偏移量
        lbl = getattr(self._ui, "label_calibOffsetValue", None)
        if lbl is None or not lbl.text() or lbl.text() == "—":
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先执行圆心检测")
            return

        try:
            text = lbl.text().strip("()")   # 格式为 "(dx, dy)"
            dx, dy = (int(v.strip()) for v in text.split(","))
        except Exception:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "偏移量格式异常，请重新检测")
            return

        logger.info(f"[校准] 自动偏移校准：偏移=({dx},{dy})")

        if self._motor_worker_x is not None and self._motor_worker_x.isRunning():
            return
        pulse_x = int(dx * self.PIXEL_TO_ANGLE_X)
        pulse_y = int(dy * self.PIXEL_TO_ANGLE_Y)
        self._motor_worker_x = _MotorMoveWorker(self._motor, slave_id=1, displacement=pulse_x)
        self._motor_worker_y = _MotorMoveWorker(self._motor, slave_id=2, displacement=pulse_y)
        def _start_y(ok, _):
            logger.info(f"[校准] X轴补偿完成: {ok}")
            self._motor_worker_y.start()
        self._motor_worker_x.finished_signal.connect(_start_y)
        def _finish_auto_offset(ok, _):
            logger.info(f"[校准] Y轴补偿完成: {ok}")
            self._resume_preview_after_motion()
        self._motor_worker_y.finished_signal.connect(_finish_auto_offset)
        self._motor_worker_x.start()

    # [电机控制] 定位：运行固定 X/Y 轴位移
    # HOME_X / HOME_Y / HOME_Z 为定位目标角度，需根据实际机械行程标定
    HOME_X = 0
    HOME_Y = 6120

    # [电机控制] 像素到角度的换算系数（需根据实际标定结果修改）
    # 含义：1 像素 对应 多少度（角度）
    PIXEL_TO_ANGLE_X = 68.2
    PIXEL_TO_ANGLE_Y = 68.2

    def _on_auto_align(self):
        """定位：将 X/Y 轴移动到固定位置。"""
        if not self._motor or not self._motor.connected:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先连接电机串口")
            return
        if self._motor_worker_x is not None and self._motor_worker_x.isRunning():
            return
        self._motor_worker_x = _MotorMoveWorker(self._motor, slave_id=1, displacement=self.HOME_X)
        self._motor_worker_y = _MotorMoveWorker(self._motor, slave_id=2, displacement=self.HOME_Y)
        def _start_y(ok, _):
            logger.info(f"[校准] X轴定位完成: {ok}")
            self._motor_worker_y.start()
        self._motor_worker_x.finished_signal.connect(_start_y)
        self._motor_worker_y.finished_signal.connect(lambda ok, _: logger.info(f"[校准] Y轴定位完成: {ok}"))
        self._motor_worker_x.start()
        logger.info(f"[校准] 定位：X={self.HOME_X}, Y={self.HOME_Y}")

    # ──────────────────────────────────────────
    #  辅助
    # ──────────────────────────────────────────

    def _read_cam_index(self) -> int:
        w = getattr(self._ui, "spinBox_calibCamIndex", None)
        return w.value() if w is not None else 0

    def _on_camera_error(self, msg: str):
        logger.error(f"[校准] 摄像头错误: {msg}")
        if self._worker is not None:
            self._worker.stop()
            try:
                self._worker.frame_ready.disconnect()
                self._worker.error.disconnect()
            except RuntimeError:
                pass
            self._worker = None
        self._previewing = False
        self._frozen = False
        self._mask_editing = False
        self._sync_mask_buttons()
        self._last_frame = None
        self._clear_preview()
        QtWidgets.QMessageBox.critical(self._ui, "摄像头错误", msg)

    def _on_resume_preview(self):
        """
        "● 实时预览" 按钮：恢复摄像头实时预览。
        若摄像头已在运行则解除冻结，恢复实时帧显示；
        若摄像头已被关闭则提示用户先开启摄像头。
        """
        if not self._previewing:
            # 摄像头已关闭，不自动重启，提示用户手动开启
            QtWidgets.QMessageBox.information(
                self._ui, "提示", "摄像头未开启，请先点击「开启摄像头」"
            )
            return

        # 解除冻结，允许新帧刷新预览
        self._frozen = False
        logger.info("[校准] 已恢复实时预览")

    # ──────────────────────────────────────────
    #  [电机控制] X / Y 轴移动
    # ──────────────────────────────────────────

    _MAX_DISPLACEMENT = 100000  # 最大允许位移值，防止误操作损坏电机

    def _parse_dist(self, line_edit_name: str) -> Optional[int]:
        """读取输入框中的位移值（mm），转为整数返回；输入非法或超范围时弹窗提示并返回 None。"""
        w = getattr(self._ui, line_edit_name, None)
        if w is None:
            return None
        try:
            val = int(float(w.text().strip()))
        except ValueError:
            QtWidgets.QMessageBox.warning(self._ui, "输入错误", "请输入有效的位移数值（正数正方向，负数反方向）")
            return None
        if abs(val) > self._MAX_DISPLACEMENT:
            QtWidgets.QMessageBox.warning(
                self._ui, "输入错误",
                f"位移值超出安全范围（最大 ±{self._MAX_DISPLACEMENT}），请重新输入"
            )
            return None
        return val

    def _on_move_x(self):
        """执行 X 轴移动（slave_id=1）。"""
        if not self._motor.connected:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先连接电机串口")
            return
        if self._motor_worker_x is not None and self._motor_worker_x.isRunning():
            return
        dist = self._parse_dist("lineEdit_calibXDist")
        if dist is None:
            return
        angle = int(dist * self.PIXEL_TO_ANGLE_X)
        btn = getattr(self._ui, "btnCalibMoveX", None)
        if btn:
            btn.setEnabled(False)
        self._motor_worker_x = _MotorMoveWorker(self._motor, slave_id=1, displacement=angle)
        self._motor_worker_x.finished_signal.connect(lambda ok, err: self._on_move_done("X", ok, err, btn))
        self._motor_worker_x.start()
        logger.info(f"[电机] X轴移动 {dist}px -> {angle}°")

    def _on_move_y(self):
        """执行 Y 轴移动（slave_id=2）。"""
        if not self._motor.connected:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先连接电机串口")
            return
        if self._motor_worker_y is not None and self._motor_worker_y.isRunning():
            return
        dist = self._parse_dist("lineEdit_calibYDist")
        if dist is None:
            return
        angle = int(dist * self.PIXEL_TO_ANGLE_Y)
        btn = getattr(self._ui, "btnCalibMoveY", None)
        if btn:
            btn.setEnabled(False)
        self._motor_worker_y = _MotorMoveWorker(self._motor, slave_id=2, displacement=angle)
        self._motor_worker_y.finished_signal.connect(lambda ok, err: self._on_move_done("Y", ok, err, btn))
        self._motor_worker_y.start()
        logger.info(f"[电机] Y轴移动 {dist}px -> {angle}°")

    def _on_move_done(self, axis: str, ok: bool, err: str, btn=None):
        if btn:
            btn.setEnabled(True)
        if not ok:
            QtWidgets.QMessageBox.warning(self._ui, f"{axis}轴移动失败", f"{axis}轴移动失败：{err}")
            logger.warning(f"[电机] {axis}轴移动失败: {err}")
        else:
            logger.info(f"[电机] {axis}轴移动完成")

    def cancel_motor_moves(self):
        """通讯中断后调用：恢复 X/Y 轴按钮可交互（_stop_flag 已让 worker 自行退出）。"""
        for btn_name in ("btnCalibMoveX", "btnCalibMoveY"):
            btn = getattr(self._ui, btn_name, None)
            if btn:
                btn.setEnabled(True)

    # ──────────────────────────────────────────
    #  [ROI功能] ROI选择与管理
    # ──────────────────────────────────────────

    def _on_select_roi(self):
        """开始ROI选择模式"""
        if not self._previewing:
            QtWidgets.QMessageBox.warning(self._ui, "提示", "请先开启摄像头")
            return

        # 保存当前冻结状态，并解除冻结以便看到实时画面
        self._frozen_before_roi = self._frozen
        self._frozen = False

        # 清除旧ROI
        self._roi = None
        self._roi_selecting = True
        self._roi_start_point = None
        self._display_roi = None
        self._roi_stabilizer.reset()
        if self._last_frame is not None:
            self._show_frame(self._last_frame)

        logger.info("[ROI] 进入ROI选择模式，请在图像上拖动鼠标框选区域")
        QtWidgets.QMessageBox.information(
            self._ui, "ROI选择",
            "请在摄像头画面上按住鼠标左键拖动，框选检测区域"
        )

    def _on_clear_roi(self):
        """清除ROI，恢复全图检测"""
        self._roi = None
        self._roi_selecting = False
        self._roi_start_point = None
        self._display_roi = None
        self._roi_stabilizer.reset()
        logger.info("[ROI] 已清除ROI，恢复全图检测")

        # 刷新显示，移除ROI遮罩（无论是否冻结都刷新一次）
        if self._last_frame is not None:
            self._show_preview_frame(self._last_frame)

    def _apply_roi_mask(self, frame: np.ndarray) -> np.ndarray:
        """
        在图像上应用ROI遮罩（ROI外部显示灰色半透明遮罩）

        参数：
            frame: 原始图像

        返回：
            添加了ROI遮罩的图像
        """
        if self._roi is None:
            return frame

        result = frame.copy()
        x, y, w, h = self._roi

        # 创建遮罩层（更暗的灰色）
        mask = np.zeros_like(result, dtype=np.uint8)
        mask[:, :] = (80, 80, 80)  # 更暗的灰色（原来是128）

        # ROI区域保持透明（不添加遮罩）
        mask[y:y+h, x:x+w] = (0, 0, 0)

        # 混合遮罩（ROI外部变暗，增加透明度）
        alpha = 0.65  # 遮罩透明度（原来是0.5，现在更不透明）
        result = cv2.addWeighted(result, 1.0, mask, alpha, 0)

        return result

    def eventFilter(self, obj, event):
        """事件过滤器：处理手动标点的鼠标/键盘事件，以及ROI框选的鼠标事件。"""
        label = getattr(self._ui, "labelCalibCameraView", None)
        if obj is not label:
            return False

        if event.type() == QtCore.QEvent.MouseMove and self._last_frame is not None:
            x, y = self._label_to_image_coords(event.pos().x(), event.pos().y())
            self._set_calib_text("labelCalibMouseInfo", f"鼠标: 图像({x}, {y})")

        # [手动标点] 委托给 MarkingController
        if self._mask_editing:
            if event.type() == QtCore.QEvent.MouseButtonPress:
                self._paint_probe_mask(event.pos().x(), event.pos().y(), int(event.button()))
                return True
            if event.type() == QtCore.QEvent.MouseMove:
                self._paint_probe_mask(event.pos().x(), event.pos().y(), int(event.buttons()))
                return True

        if self._waiting_for_tape_pick and event.type() == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.LeftButton and self._last_frame is not None:
                x, y = self._label_to_image_coords(event.pos().x(), event.pos().y())
                self._tape_hsv = sample_hsv_color(self._last_frame, x, y)
                self._waiting_for_tape_pick = False
                self._set_tape_status(f"HSV: {self._tape_hsv}")
                logger.info(f"[calib] tape hsv={self._tape_hsv} at ({x}, {y})")
                return True

        if self._marking.handle_event(event):
            return True

        # [ROI框选] 原有逻辑
        if not self._roi_selecting:
            return False

        if event.type() == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.LeftButton:
                self._roi_start_point = (event.pos().x(), event.pos().y())
                return True

        elif event.type() == QtCore.QEvent.MouseMove:
            if self._roi_start_point is not None and self._last_frame is not None:
                current_point = (event.pos().x(), event.pos().y())
                self._draw_roi_preview(self._roi_start_point, current_point)
                return True

        elif event.type() == QtCore.QEvent.MouseButtonRelease:
            if event.button() == QtCore.Qt.LeftButton and self._roi_start_point is not None:
                end_point = (event.pos().x(), event.pos().y())
                self._finalize_roi(self._roi_start_point, end_point)
                # 注意：_finalize_roi 内部会设置 self._roi_selecting = False
                self._roi_start_point = None
                return True

        return False

    def _label_to_image_coords(self, label_x: int, label_y: int, use_display_roi: bool = True) -> tuple:
        """
        将Label坐标转换为图像坐标
        考虑图像在Label中的缩放和居中偏移

        参数：
            label_x, label_y: Label上的坐标

        返回：
            (image_x, image_y): 图像上的坐标
        """
        if self._last_frame is None:
            return (0, 0)

        label = getattr(self._ui, "labelCalibCameraView", None)
        if label is None:
            return (0, 0)

        frame_h, frame_w = self._last_frame.shape[:2]
        label_w, label_h = label.width(), label.height()

        # 计算图像在Label中的实际显示尺寸（等比缩放）
        scale = min(label_w / frame_w, label_h / frame_h)
        display_w = int(frame_w * scale)
        display_h = int(frame_h * scale)

        # 计算图像在Label中的偏移（居中显示）
        offset_x = (label_w - display_w) // 2
        offset_y = (label_h - display_h) // 2

        # 转换坐标：Label坐标 -> 显示图像坐标 -> 原始图像坐标
        img_x = int((label_x - offset_x) / scale)
        img_y = int((label_y - offset_y) / scale)

        # 限制在图像范围内
        full_h, full_w = self._last_frame.shape[:2]
        img_x = max(0, min(img_x, full_w - 1))
        img_y = max(0, min(img_y, full_h - 1))

        return (img_x, img_y)

    def _draw_roi_preview(self, start: tuple, end: tuple):
        """实时绘制ROI框选预览（只显示灰色遮罩），带节流优化"""
        if self._last_frame is None:
            return

        # 节流：限制刷新频率，避免闪烁（每33ms最多刷新一次，约30fps）
        import time
        current_time = time.time()
        if current_time - self._last_preview_time < 0.033:
            return
        self._last_preview_time = current_time

        # 使用新的坐标转换函数
        x1, y1 = self._label_to_image_coords(start[0], start[1], use_display_roi=False)
        x2, y2 = self._label_to_image_coords(end[0], end[1], use_display_roi=False)

        # 确保坐标顺序正确
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)

        preview = self._last_frame.copy()
        if w > 0 and h > 0:
            cv2.rectangle(preview, (x, y), (x + w, y + h), (165, 185, 185), 1, lineType=cv2.LINE_AA)

        # 显示
        self._display_roi = None
        self._show_frame(preview)

    def _finalize_roi(self, start: tuple, end: tuple):
        """完成ROI选择，保存ROI区域"""
        if self._last_frame is None:
            return

        # 使用新的坐标转换函数
        x1, y1 = self._label_to_image_coords(start[0], start[1], use_display_roi=False)
        x2, y2 = self._label_to_image_coords(end[0], end[1], use_display_roi=False)

        # 确保坐标顺序正确
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)

        # 验证ROI有效性
        if w < 10 or h < 10:
            QtWidgets.QMessageBox.warning(
                self._ui, "ROI无效",
                "选择的区域太小，请重新框选（最小10x10像素）"
            )
            # 框选失败，退出框选模式，恢复之前的冻结状态
            self._roi_selecting = False
            self._frozen = self._frozen_before_roi
            return

        # 限制ROI在图像范围内
        frame_h, frame_w = self._last_frame.shape[:2]
        x = max(0, min(x, frame_w - 1))
        y = max(0, min(y, frame_h - 1))
        w = min(w, frame_w - x)
        h = min(h, frame_h - y)

        self._roi = (x, y, w, h)
        logger.info(f"[ROI] ROI已设置: x={x}, y={y}, w={w}, h={h}")

        # 框选完成，退出框选模式，恢复之前的冻结状态
        self._roi_selecting = False
        self._frozen = self._frozen_before_roi

        # 显示ROI遮罩（框选完成后立即显示）
        self._show_preview_frame(self._last_frame, self._roi)

        QtWidgets.QMessageBox.information(
            self._ui, "ROI设置成功",
            f"ROI区域已设置：\n位置: ({x}, {y})\n大小: {w} x {h}\n\n"
            "后续圆心检测将只在此区域内进行"
        )
