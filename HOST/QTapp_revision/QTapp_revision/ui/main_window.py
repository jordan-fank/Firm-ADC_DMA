# -*- coding: utf-8 -*-
import sys
import os
import time
import numpy as np
import warnings
import logging

warnings.filterwarnings("ignore", category=DeprecationWarning)

from PyQt5 import QtWidgets, QtGui, QtCore, uic
from PyQt5.QtWidgets import QMessageBox, QFileDialog
from PyQt5.QtCore import Qt

from algorithms.cylinder_3d import run_pipeline_3d
from core.serial_controller import SerialController
from core.imaging_pipeline import ImagingPipeline
from core.monitor_manager import MonitorManager
from core.segmentation_controller import SegmentationController
from core.calib_controller import CalibController
from core.motor_controller import MotorController  # [电机控制] 全局电机控制器
from core.tanzheng_controller import TanzhengController  # [探针控制]
import serial.tools.list_ports  # [电机控制] 串口扫描
from config.config_manager import get_config_manager
from utils.resource_utils import resource_path
from ui.widgets.zoomable_label import ZoomableLabel

# ===== 配置日志系统 =====
def get_app_dir() -> str:
    """
    获取程序所在目录：
    - 打包后使用 sys.executable 所在目录
    - 开发模式下使用当前工作目录
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")


def get_output_dir() -> str:
    """
    默认输出目录：<app_dir>/output
    如不存在则自动创建。
    """
    out_dir = os.path.join(get_app_dir(), "output")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(get_app_dir(), 'app.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class AdaptiveContentWidget(QtWidgets.QWidget):
    """Design canvas whose minimum size expands to match the screen ratio."""

    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)


class _MotorConnectWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(bool, str, str)  # ok, err, port

    def __init__(self, controller, port: str):
        super().__init__()
        self._controller = controller
        self._port = port

    def run(self):
        ok, err = self._controller.connect(self._port)
        self.finished.emit(ok, err, self._port)


class _MotorInterruptWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(list)  # errors list

    def __init__(self, controller):
        super().__init__()
        self._controller = controller

    def run(self):
        errors = []
        for slave_id in (1, 2, 3):
            ok, err = self._controller.release_trigger(slave_id=slave_id)
            if not ok:
                errors.append(f"slave {slave_id}: {err}")
        self.finished.emit(errors)


class _MotorMoveWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(bool, str)  # ok, err

    def __init__(self, controller, slave_id: int, displacement: int, timeout: float = 30.0):
        super().__init__()
        self._controller = controller
        self._slave_id = slave_id
        self._displacement = displacement
        self._timeout = timeout

    def run(self):
        ok, err = self._controller.move_motor_and_wait(
            slave_id=self._slave_id,
            displacement=self._displacement,
            timeout=self._timeout
        )
        self.finished.emit(ok, err)


class _SerialSaveWorker(QtCore.QThread):
    succeeded = QtCore.pyqtSignal(str)   # target_path
    failed = QtCore.pyqtSignal(str, str) # target_path, error

    def __init__(self, controller, text: str, target_path: str):
        super().__init__()
        self._controller = controller
        self._text = text
        self._target_path = target_path

    def run(self):
        ok, err = self._controller.write_data_file(self._text, self._target_path)
        if ok:
            self.succeeded.emit(self._target_path)
        else:
            self.failed.emit(self._target_path, err)


class _TanzhengConnectWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(bool, str)  # connected, port

    def __init__(self, controller, port: str):
        super().__init__()
        self._controller = controller
        self._port = port

    def run(self):
        self._controller.connect(self._port)
        self.finished.emit(self._controller.connected, self._port)


class _TanzhengWriteWorker(QtCore.QThread):
    def __init__(self, controller, action: str):
        super().__init__()
        self._controller = controller
        self._action = action

    def run(self):
        if self._action == "rotate":
            self._controller.rotate()
        else:
            self._controller.stop()


class MainWindow(QtWidgets.QMainWindow):
    """
    源泰工业内壁成像系统 - 主窗口

    职责：
        - UI协调和事件分发
        - 窗口缩放和布局管理
        - 页面路由（机器控制、成像、分割）
        - 图像预览和操作

    核心组件：
        - SerialController: 串口通信管理
        - ImagingPipeline: 2D/3D成像处理
        - MonitorManager: 硬件监控和服务器状态

    架构说明：
        MainWindow作为UI协调层，将具体业务逻辑委托给专职控制器，
        保持代码清晰和职责分离。
    """

    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent)

        # ===== 状态变量 =====
        self._last_xyz_path = None  # 最近生成的点云文件路径

        # ===== 加载配置 =====
        self.config = get_config_manager()
        logger.info("[应用] 配置加载完成")

        # ===== 使用自适应比例画布加载UI =====
        self._setup_scalable_ui()

        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._update_scale)

        # 2. 图标
        win_icon = QtGui.QIcon(resource_path("3.png"))
        if not win_icon.isNull():
            self.setWindowIcon(win_icon)

        # 3. 顶部 logo
        self._setup_header_logo("2.png")

        # 4. ScrollArea 重建
        self._rebuild_scroll_preview()

        # 默认输出目录固定为程序目录下 output，避免恢复上次临时测试路径
        if hasattr(self, "lineEditOutputDir"):
            self.lineEditOutputDir.setText(get_output_dir())

        # 5. 默认图片
        self._load_default_images()

        # 6. 阈值联动
        self._setup_segmentation_controls()

        # 7. 布局比例
        self._setup_layout_stretch()

        # 8. 串口控制器（机器控制核心）
        self.serial_controller = SerialController(self, self, self.config, get_app_dir)
        self.serial_controller.initialize()
        self.serial_controller.speed_updated.connect(self._on_speed_updated)
        self._init_speed_display()

        # 9. 成像管道（2D/3D成像处理）
        self.imaging_pipeline = ImagingPipeline(
            self, self, self.config,
            self.serial_controller,
            get_app_dir, get_output_dir
        )

        # 9. 页面路由（3 个页面，默认机器控制）
        self._setup_routing()

        # 10. 安装全局事件过滤器，监听所有交互事件
        self.installEventFilter(self)

        # 11. CloudCompare 路径
        self._init_cloudcompare_path()

        # 12. 图像处理相关按钮
        self._connect_ui_signals()

        # 13. 硬件监控和服务器状态管理
        self.monitor_manager = MonitorManager(self, self, self.config)
        self.monitor_manager.setup_hardware_monitoring()

        # 14. 机器控制按钮连接
        self._init_machine_control_signals()

        # 15. 分割功能
        self.segmentation_controller = SegmentationController(
            self, self, self.config, get_output_dir
        )
        self.segmentation_controller.initialize()

        # [电机控制] 全局电机控制器（供校准页面和机器控制页面共用）
        self.motor_controller = MotorController()
        self._current_z_pos = 0  # 跟踪 Z 轴当前位置（相对于软件启动时的位置）
        self._init_motor_signals()

        # [探针控制]
        self.tanzheng_controller = TanzhengController(self)
        self._init_tanzheng_signals()

        # 16. 校准功能（摄像头圆心检测）
        self.calib_controller = CalibController(self, self, self.motor_controller)
        self.calib_controller.initialize()

        # 当前预览状态
        self._current_qimage = None
        self._current_pixmap = None
        self._default_pixmap = None
        self._flip_horizontal = False

        # ===== 窗口设置 =====
        self.setWindowTitle("源泰工业内壁成像系统")

        # 设置初始窗口尺寸为设计尺寸
        self.resize(1360, 800)

        # 将窗口居中显示在屏幕上
        self._center_on_screen()

        # 允许窗口和内部布局跟随当前屏幕的可用区域伸缩
        size_policy = self.sizePolicy()
        size_policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Expanding)
        size_policy.setVerticalPolicy(QtWidgets.QSizePolicy.Expanding)
        self.setSizePolicy(size_policy)

        # ===== 连接窗口缩放按钮 =====
        self._setup_window_scale_button()

    # ===== 自适应比例缩放相关方法 =====

    def _setup_scalable_ui(self):
        """
        加载 Designer UI，并挂载到动态比例画布中。

        以 Designer 中央内容的真实最小尺寸作为基准，根据 viewport 的
        比例扩展宽度或高度。画布整体等比缩放后，字体和组件会同步放大，
        同时避免底部面板超出画布后被裁切。
        """
        self._base_design_width = 1360
        self._base_design_height = 800
        self._design_width = self._base_design_width
        self._design_height = self._base_design_height
        self.content_widget = AdaptiveContentWidget(
            self._design_width, self._design_height
        )
        self.scene = QtWidgets.QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, self._design_width, self._design_height)
        self.proxy_widget = self.scene.addWidget(self.content_widget)

        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.SmoothPixmapTransform
            | QtGui.QPainter.TextAntialiasing
        )
        self.view.setAlignment(QtCore.Qt.AlignCenter)
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view.setContentsMargins(0, 0, 0, 0)
        self.view.setStyleSheet(
            "QGraphicsView { border: none; margin: 0px; padding: 0px; }"
        )
        self.setCentralWidget(self.view)

        temp_main_window = QtWidgets.QMainWindow()
        uic.loadUi(resource_path("AppMain.ui"), temp_main_window)

        for attr_name, attr_value in temp_main_window.__dict__.items():
            if not attr_name.startswith('_'):
                setattr(self, attr_name, attr_value)

        central = temp_main_window.takeCentralWidget()
        if central is not None:
            self.content_widget.main_layout.addWidget(central)
            self.centralwidget = central
            self.content_widget.main_layout.activate()
            central.layout().activate()
            minimum = central.minimumSizeHint()
            layout_minimum = central.layout().minimumSize()
            self._base_design_width = max(
                self._base_design_width,
                minimum.width(),
                layout_minimum.width(),
            )
            self._base_design_height = max(
                self._base_design_height,
                minimum.height(),
                layout_minimum.height(),
            )

        self._update_scale()

    def _update_scale(self):
        """Match the design canvas ratio to the viewport, then scale uniformly."""
        if not hasattr(self, "view"):
            return

        viewport_size = self.view.viewport().size()
        if viewport_size.width() <= 0 or viewport_size.height() <= 0:
            return

        viewport_ratio = viewport_size.width() / viewport_size.height()
        base_ratio = self._base_design_width / self._base_design_height
        if viewport_ratio >= base_ratio:
            design_width = round(self._base_design_height * viewport_ratio)
            design_height = self._base_design_height
        else:
            design_width = self._base_design_width
            design_height = round(self._base_design_width / viewport_ratio)

        if (
            design_width != self._design_width
            or design_height != self._design_height
        ):
            self._design_width = design_width
            self._design_height = design_height
            self.content_widget.setFixedSize(design_width, design_height)
            self.content_widget.setMinimumSize(design_width, design_height)
            self.proxy_widget.setMinimumSize(design_width, design_height)
            self.proxy_widget.setMaximumSize(design_width, design_height)
            self.scene.setSceneRect(0, 0, design_width, design_height)

        scale = min(
            viewport_size.width() / self._design_width,
            viewport_size.height() / self._design_height,
        )
        transform = QtGui.QTransform()
        transform.scale(scale, scale)
        self.view.setTransform(transform)
        self.view.centerOn(self.scene.sceneRect().center())

    def resizeEvent(self, event):
        """Refresh the adaptive canvas after the window geometry changes."""
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start(50)
        else:
            self._update_scale()

    def changeEvent(self, event):
        """
        重写窗口状态变化事件

        处理窗口状态变化（最大化、最小化等）
        """
        super().changeEvent(event)

    def _center_on_screen(self):
        """
        将窗口居中显示在屏幕上

        实现步骤：
        1. 获取屏幕的几何尺寸
        2. 获取窗口的几何尺寸
        3. 计算屏幕中心点
        4. 将窗口移动到屏幕中心
        """
        # 获取屏幕几何信息
        screen = QtWidgets.QApplication.desktop().screenGeometry()
        # 获取窗口几何信息
        geo = self.frameGeometry()
        # 将窗口移动到屏幕中心
        geo.moveCenter(screen.center())
        # 应用新位置
        self.move(geo.topLeft())

    def _setup_window_scale_button(self):
        """
        连接窗口缩放按钮（放大/缩小按钮）

        - 放大按钮：每次放大25%，最大不超过屏幕尺寸
        - 缩小按钮：每次缩小25%，最小100%
        - 中间显示当前缩放比例
        """
        # 基础缩放比例（从100%开始）
        self._base_scale_levels = [1.0, 1.25, 1.5, 1.75, 2.0]
        self._current_scale_index = 0  # 当前索引，初始为100%

        # 计算屏幕可用尺寸，确定最大允许的缩放比例
        self._calculate_max_scale()

        # 连接 UI 中的按钮
        if hasattr(self, "btnWindowZoomIn"):
            self.btnWindowZoomIn.clicked.connect(self._zoom_in_window)

        if hasattr(self, "btnWindowZoomOut"):
            self.btnWindowZoomOut.clicked.connect(self._zoom_out_window)
            # 初始时禁用缩小按钮（已经是100%）
            self.btnWindowZoomOut.setEnabled(False)

        self.log("[窗口] 缩放按钮已连接")

    def _calculate_max_scale(self):
        """
        计算最大允许的缩放比例（不超过屏幕可用尺寸）
        """
        # 获取屏幕可用区域（排除任务栏）
        screen = QtWidgets.QApplication.desktop().availableGeometry()

        # 计算屏幕可以容纳的最大缩放比例（留出一些边距）
        max_scale_width = (screen.width() - 40) / self._design_width
        max_scale_height = (screen.height() - 40) / self._design_height
        max_scale = min(max_scale_width, max_scale_height)

        # 过滤出不超过屏幕尺寸的缩放级别
        self._scale_levels = [s for s in self._base_scale_levels if s <= max_scale]

        # 如果所有预设都超过屏幕，至少保留100%
        if not self._scale_levels:
            self._scale_levels = [1.0]

        self.log(f"[窗口] 屏幕可用尺寸: {screen.width()}x{screen.height()}, "
                f"最大缩放: {int(max_scale * 100)}%, "
                f"可用级别: {[int(s*100) for s in self._scale_levels]}%")

    def _zoom_in_window(self):
        """
        放大窗口（每次增加一个级别）

        最大不超过屏幕可用尺寸
        """
        # 如果已经是最大级别，不再放大
        if self._current_scale_index >= len(self._scale_levels) - 1:
            self.log("[窗口] 已达到最大缩放级别")
            return

        # 放大到下一个级别
        self._current_scale_index += 1
        self._apply_scale_change()

    def _zoom_out_window(self):
        """
        缩小窗口（每次减少一个级别）

        最小100%
        """
        # 如果已经是最小级别（100%），不再缩小
        if self._current_scale_index <= 0:
            self.log("[窗口] 已达到最小缩放级别（100%）")
            return

        # 缩小到上一个级别
        self._current_scale_index -= 1
        self._apply_scale_change()

    def _apply_scale_change(self):
        """
        应用缩放变化

        更新窗口尺寸、按钮状态和显示文本
        """
        scale = self._scale_levels[self._current_scale_index]

        # 计算新的窗口尺寸
        new_width = int(self._design_width * scale)
        new_height = int(self._design_height * scale)

        # 调整窗口大小
        self.resize(new_width, new_height)

        # 将窗口居中
        self._center_on_screen()

        # 更新显示文本
        scale_percent = int(scale * 100)
        if hasattr(self, "labelWindowScale"):
            self.labelWindowScale.setText(f"{scale_percent}%")

        # 更新按钮状态
        self._update_zoom_buttons_state()

        # 记录日志
        self.log(f"[窗口] 缩放比例已调整为 {scale_percent}%")

    def _update_zoom_buttons_state(self):
        """
        更新放大/缩小按钮的启用状态

        - 缩小按钮：当前级别 > 0 时启用
        - 放大按钮：当前级别 < 最大级别时启用
        """
        if hasattr(self, "btnWindowZoomOut"):
            # 如果是最小级别（100%），禁用缩小按钮
            self.btnWindowZoomOut.setEnabled(self._current_scale_index > 0)

        if hasattr(self, "btnWindowZoomIn"):
            # 如果是最大级别，禁用放大按钮
            self.btnWindowZoomIn.setEnabled(
                self._current_scale_index < len(self._scale_levels) - 1
            )

    # ---------- 串口初始化 ----------

    # ---------- UI 信号（图像处理部分） ----------
    def _connect_ui_signals(self):
        """图像处理相关的 signal-slot 连接。"""
        # 文件与成像
        if hasattr(self, "browseButton"):
            self.browseButton.clicked.connect(self.browse_file)
        if hasattr(self, "image2DButton"):
            self.image2DButton.clicked.connect(self.handle_image2d)
        if hasattr(self, "image3DButton"):
            self.image3DButton.clicked.connect(self.handle_image3d)
        if hasattr(self, "btnBrowseOutputDir"):
            self.btnBrowseOutputDir.clicked.connect(self.choose_output_dir)

        # 对比度增强控件初始化与双向绑定
        if hasattr(self, "gaohuakuai") and hasattr(self, "gaozhi"):
            self.gaohuakuai.setRange(0, 100)
            self.gaohuakuai.setValue(100)
            self.gaozhi.setText("100")
            self.gaohuakuai.valueChanged.connect(lambda v: self.gaozhi.setText(str(v)))
            self.gaozhi.editingFinished.connect(lambda: self.gaohuakuai.setValue(int(self.gaozhi.text())) if self.gaozhi.text().isdigit() else None)
        if hasattr(self, "dihuakuai") and hasattr(self, "dizhi"):
            self.dihuakuai.setRange(0, 100)
            self.dihuakuai.setValue(0)
            self.dizhi.setText("0")
            self.dihuakuai.valueChanged.connect(lambda v: self.dizhi.setText(str(v)))
            self.dizhi.editingFinished.connect(lambda: self.dihuakuai.setValue(int(self.dizhi.text())) if self.dizhi.text().isdigit() else None)
        if hasattr(self, "beishuhuakuai") and hasattr(self, "beishuzhi"):
            self.beishuhuakuai.setRange(10, 100)
            self.beishuhuakuai.setValue(10)
            self.beishuzhi.setText("1")
            self.beishuhuakuai.valueChanged.connect(lambda v: self.beishuzhi.setText(str(v / 10)))
            self.beishuzhi.editingFinished.connect(lambda: self.beishuhuakuai.setValue(int(float(self.beishuzhi.text()) * 10)) if self.beishuzhi.text() else None)
        if hasattr(self, "dshuakuai") and hasattr(self, "dszhi"):
            self.dshuakuai.setRange(1, 100)
            ds_init = self.config.get("imaging.ds_initial", 1.0)
            self.dshuakuai.setValue(int(ds_init * 100))
            self.dszhi.setText(str(round(ds_init, 2)))
            self.dshuakuai.valueChanged.connect(lambda v: self.dszhi.setText(str(round(v / 100, 2))))
            self.dszhi.editingFinished.connect(lambda: self.dshuakuai.setValue(int(float(self.dszhi.text()) * 100)) if self.dszhi.text() else None)

        # 图像操作
        if hasattr(self, "btnFlipImage"):
            self.btnFlipImage.clicked.connect(self.flip_image_horizontal)
        if hasattr(self, "btnSaveImage"):
            self.btnSaveImage.clicked.connect(self.save_current_image)
        if hasattr(self, "btnClosePreview"):
            self.btnClosePreview.clicked.connect(self.reset_preview)

    # ---------- 机器控制：信号连接 ----------
    def _init_machine_control_signals(self):
        """
        机器控制页的按钮连接：
        - 串口开关 / 清空
        - 上升 / 下降 / 复位 / 测量命令
        - 保存采集数据
        """
        # 串口区
        if hasattr(self, "OpenButton"):
            self.OpenButton.clicked.connect(self.on_open_serial)
        if hasattr(self, "CloseButton"):
            self.CloseButton.clicked.connect(self.on_close_serial)
        if hasattr(self, "ClearButton"):
            self.ClearButton.clicked.connect(self.on_clear_serial_view)

        # 手动控制
        if hasattr(self, "SendButton_3"):  # 下降
            self.SendButton_3.clicked.connect(self.on_move_down)
        if hasattr(self, "SendButton_2"):  # 上升
            self.SendButton_2.clicked.connect(self.on_move_up)

        # 动态添加“电机复位”按钮到“发送测量”按钮所在的布局中
        if hasattr(self, "horizontalLayout_measureAction"):
            # 缩小主按钮宽度以给复位按钮留出空间
            if hasattr(self, "SendButton"):
                self.SendButton.setFixedWidth(210)  # 原来是314

            self.btnMotorReset = QtWidgets.QPushButton("电机复位")
            self.btnMotorReset.setFixedWidth(94)
            self.btnMotorReset.setFixedHeight(26)
            # 设置样式，使其与旁边的“发送测量”按钮颜色一致
            self.btnMotorReset.setStyleSheet("""
                QPushButton {
                    background-color: #416E89;
                    color: #FFFFFF;
                    border: 1px solid #416E89;
                    border-radius: 5px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: #315F7D;
                    border-color: #315F7D;
                }
                QPushButton:pressed {
                    background-color: #294E67;
                    border-color: #294E67;
                }
                QPushButton:disabled {
                    background-color: #D9E2EC;
                    border-color: #CBD5E1;
                    color: #94A3B8;
                }
            """)
            self.horizontalLayout_measureAction.addWidget(self.btnMotorReset)
            self.btnMotorReset.clicked.connect(self.on_motor_reset)

        if hasattr(self, "btnHome"):
            self.btnHome.clicked.connect(self.on_home)
        if hasattr(self, "btnEmergencyStop"):
            self.btnEmergencyStop.clicked.connect(self.on_emergency_stop)
        if hasattr(self, "ResetButton"):
            self.ResetButton.clicked.connect(self.on_reset_device)

        # 自动运动参数
        if hasattr(self, "SendButton"):
            self.SendButton.clicked.connect(self.on_send_measure_command)

        # 采集数据保存 - SaveButton用于保存
        if hasattr(self, "SaveButton"):
            self.SaveButton.clicked.connect(self.on_save_serial_data)


    # ---------- 串口接收 -> 内部缓冲区（定时批量显示） ----------
    # ---------- 串口相关方法（委托给 SerialController） ----------
    def _on_serial_ready_read(self):
        """串口数据接收（由SerialController内部处理，此方法保留用于兼容性）"""
        pass

    def updateSerialPortStatus(self, is_open: bool):
        """更新串口状态（委托给SerialController）"""
        self.serial_controller._update_status(is_open)

    def on_open_serial(self):
        """打开串口（委托给SerialController）"""
        # 检查是否与电机串口冲突
        if self.motor_controller.connected and self.motor_controller._port:
            combo = getattr(self, "SerialcomboBox", None)
            selected = combo.currentText().strip() if combo else ""
            if selected.upper() == self.motor_controller._port.upper():
                QMessageBox.warning(self, "串口冲突", f"端口 {self.motor_controller._port} 已被电机控制器占用，请选择其他端口")
                return
        self.serial_controller.open_serial()

    def on_close_serial(self):
        """关闭串口（委托给SerialController）"""
        self.serial_controller.close_serial()

    def on_clear_serial_view(self):
        """清空串口显示（委托给SerialController）"""
        self.serial_controller.clear_view()

    def _ensure_serial_open(self) -> bool:
        """确保串口已打开（委托给SerialController）"""
        return self.serial_controller.ensure_open()

    def _validate_distance(self, value: str, param_name: str) -> tuple[bool, float]:
        """
        验证距离参数输入

        Args:
            value: 用户输入的值
            param_name: 参数名称（用于错误提示）

        Returns:
            (是否有效, 转换后的浮点数)
        """
        try:
            dist = float(value)
            if dist <= 0:
                QMessageBox.warning(self, "输入错误", f"{param_name}必须是正数")
                return False, 0.0
            if dist > 10000:  # 设置合理上限（10米）
                QMessageBox.warning(self, "输入错误", f"{param_name}超出范围（最大10000mm）")
                return False, 0.0
            return True, dist
        except ValueError:
            QMessageBox.warning(self, "输入错误", f"{param_name}必须是有效的数字")
            return False, 0.0

    def on_move_up(self):
        """Z轴上升：使用Modbus控制（slave_id=3，正数位移）"""
        if not self.motor_controller.connected:
            QMessageBox.warning(self, "提示", "请先连接电机串口")
            return

        # 检查是否有Z轴移动正在进行
        if hasattr(self, "_z_move_worker") and self._z_move_worker.isRunning():
            QMessageBox.warning(self, "提示", "Z轴正在移动中，请等待完成")
            return

        dist_str = self.RiseLineEdit.text().strip() if hasattr(self, "RiseLineEdit") else ""
        if not dist_str:
            QMessageBox.warning(self, "警告", "请输入上升距离（mm）")
            return

        # 验证输入
        valid, dist = self._validate_distance(dist_str, "上升距离")
        if not valid:
            return

        # 上升为正数位移
        displacement = int(dist) * 90

        # 禁用按钮，防止重复点击
        btn = getattr(self, "SendButton_2", None)
        if btn:
            btn.setEnabled(False)

        # 启动后台线程
        self._z_move_worker = _MotorMoveWorker(
            self.motor_controller,
            slave_id=3,
            displacement=displacement,
            timeout=30.0
        )
        self._z_move_worker.finished.connect(
            lambda ok, err: self._on_z_move_done("上升", dist, ok, err, btn)
        )
        self._z_move_worker.start()
        self.log(f"[电机] Z轴上升开始: {dist}mm")
        logger.info(f"[电机] Z轴上升开始: {dist}mm (位移={displacement})")

    def on_move_down(self):
        """Z轴下降：使用Modbus控制（slave_id=3，负数位移）"""
        if not self.motor_controller.connected:
            QMessageBox.warning(self, "提示", "请先连接电机串口")
            return

        # 检查是否有Z轴移动正在进行
        if hasattr(self, "_z_move_worker") and self._z_move_worker.isRunning():
            QMessageBox.warning(self, "提示", "Z轴正在移动中，请等待完成")
            return

        dist_str = self.DropLineEdit.text().strip() if hasattr(self, "DropLineEdit") else ""
        if not dist_str:
            QMessageBox.warning(self, "警告", "请输入下降距离（mm）")
            return

        # 验证输入
        valid, dist = self._validate_distance(dist_str, "下降距离")
        if not valid:
            return

        # 下降为负数位移
        displacement = -int(dist) * 90

        # 禁用按钮，防止重复点击
        btn = getattr(self, "SendButton_3", None)
        if btn:
            btn.setEnabled(False)

        # 启动后台线程
        self._z_move_worker = _MotorMoveWorker(
            self.motor_controller,
            slave_id=3,
            displacement=displacement,
            timeout=30.0
        )
        self._z_move_worker.finished.connect(
            lambda ok, err: self._on_z_move_done("下降", dist, ok, err, btn)
        )
        self._z_move_worker.start()
        self.log(f"[电机] Z轴下降开始: {dist}mm")
        logger.info(f"[电机] Z轴下降开始: {dist}mm (位移={displacement})")

    def on_motor_reset(self):
        """电机复位：回到初始位置（self._current_z_pos = 0）"""
        if not self.motor_controller.connected:
            QMessageBox.warning(self, "提示", "请先连接电机串口")
            return

        if hasattr(self, "_z_move_worker") and self._z_move_worker.isRunning():
            QMessageBox.warning(self, "提示", "电机正在移动中，请等待完成")
            return

        if self._current_z_pos == 0:
            QMessageBox.information(self, "提示", "电机已在初始位置")
            return

        # 计算回到 0 位所需的位移
        displacement = -self._current_z_pos
        dist_mm = abs(displacement) / 90

        # 禁用复位按钮
        if hasattr(self, "btnMotorReset"):
            self.btnMotorReset.setEnabled(False)

        # 启动后台线程执行复位
        self._z_move_worker = _MotorMoveWorker(
            self.motor_controller,
            slave_id=3,
            displacement=displacement,
            timeout=60.0  # 复位可能路径较长，给更长超时
        )
        self._z_move_worker.finished.connect(
            lambda ok, err: self._on_z_move_done("复位", dist_mm, ok, err, getattr(self, "btnMotorReset", None))
        )
        self._z_move_worker.start()
        self.log(f"[电机] Z轴复位开始: 移动 {dist_mm:.2f}mm 以回到初始位置")
        logger.info(f"[电机] Z轴复位开始: 移动 {displacement} 脉冲以回到初始位置")

    def _on_z_move_done(self, direction: str, dist: float, ok: bool, err: str, btn):
        """Z轴移动完成回调"""
        # 恢复按钮
        if btn:
            btn.setEnabled(True)

        if ok:
            self.log(f"[电机] Z轴{direction}完成: {dist:.2f}mm")
            logger.info(f"[电机] Z轴{direction}完成: {dist}mm")
            # 更新当前累计位移
            if direction == "上升":
                self._current_z_pos += int(dist) * 90
            elif direction == "下降":
                self._current_z_pos -= int(dist) * 90
            elif direction == "复位":
                self._current_z_pos = 0
        else:
            self.log(f"[电机] Z轴{direction}失败: {err}")
            logger.error(f"[电机] Z轴{direction}失败: {err}")
            QMessageBox.warning(self, "失败", f"Z轴{direction}失败：{err}")

    def on_reset_device(self):
        """等价 C++ on_ResetButton_clicked：E + 0 + '\n'"""
        if not self._ensure_serial_open():
            return

        data = bytearray()
        data.extend(b"E")
        data.append(0)
        data.append(ord("\n"))
        self.serial_controller.write_data(bytes(data))
        self.log("[SERIAL TX] E (设备复位)")

    def on_home(self):
        """
        回零位按钮：
        原 C++ 版本未给出具体协议，这里只在日志中提示，
        如有具体指令（例如 'H'），可在此补充 serial.write(...)
        """
        self.log("[机器控制] 回零位按钮被点击（尚未定义串口协议）")
        QMessageBox.information(self, "提示", "回零位指令未在协议中定义，如需控制请补充具体协议。")

    def on_emergency_stop(self):
        """
        急停按钮：
        原 C++ 版本未给出具体协议，为安全起见不发送任何串口数据，
        仅提示用户。若有硬件急停指令（例如 'S'），可在此补充。
        """
        self.log("[机器控制] 急停按钮被点击（未向串口发送命令）")
        QMessageBox.warning(self, "提示", "急停请优先使用硬件急停开关；如有软件协议可在代码中补充。")

    # ---------- 成像相关方法（委托给 ImagingPipeline） ----------
    def on_send_measure_command(self):
        """发送测量命令（委托给ImagingPipeline）"""
        self.imaging_pipeline.send_measure_command()

    def _read_hex_file(self, file_path: str) -> np.ndarray:
        """读取HEX文件（委托给ImagingPipeline）"""
        return self.imaging_pipeline.read_hex_file(file_path)

    def _preprocess_to_uint8(self, x: np.ndarray) -> np.ndarray:
        """预处理图像数据（委托给ImagingPipeline）"""
        return self.imaging_pipeline.preprocess_to_uint8(x)

    def handle_image2d(self, base_name=None):
        """处理2D成像（委托给ImagingPipeline）"""
        self.imaging_pipeline.handle_image_2d(base_name)

    def handle_image3d(self, use_timestamp=False):
        """处理3D成像（委托给ImagingPipeline）"""
        self.imaging_pipeline.handle_image_3d(use_timestamp)

    def _auto_generate_2d_image(self):
        """自动生成2D图像（委托给ImagingPipeline）"""
        self.imaging_pipeline.auto_generate_2d_image()

    # ---------- 采集数据保存路径选择 ----------
    def on_browse_save_path(self):
        """浏览并选择保存路径（目录或文件）"""
        if hasattr(self, "checkAutoName") and self.checkAutoName.isChecked():
            # 自动命名模式：选择目录
            path = QFileDialog.getExistingDirectory(
                self, "选择保存目录",
                self.SaveLineEdit.text().strip() or get_output_dir()
            )
            if path:
                self.SaveLineEdit.setText(path)
                self.log(f"[保存设置] 保存目录: {path}")
        else:
            # 手动命名模式：选择文件
            path, _ = QFileDialog.getSaveFileName(
                self, "选择保存文件",
                self.SaveLineEdit.text().strip() or os.path.join(get_output_dir(), "data.txt"),
                "Text Files (*.txt);;All Files (*)"
            )
            if path:
                self.SaveLineEdit.setText(path)
                self.log(f"[保存设置] 保存文件: {path}")


    # ---------- 采集数据保存 ----------
    def on_save_serial_data(self):
        """保存串口数据：主线程处理路径/对话框，子线程执行文件写入。"""
        if hasattr(self, "_serial_save_worker") and self._serial_save_worker.isRunning():
            return
        text, target_path = self.serial_controller.prepare_save()
        if target_path is None:
            return

        btn = getattr(self, "SaveButton", None)
        if btn:
            btn.setEnabled(False)

        self._serial_save_worker = _SerialSaveWorker(self.serial_controller, text, target_path)
        self._serial_save_worker.succeeded.connect(self._on_serial_save_succeeded)
        self._serial_save_worker.failed.connect(self._on_serial_save_failed)
        self._serial_save_worker.start()

    def _on_serial_save_succeeded(self, target_path: str):
        btn = getattr(self, "SaveButton", None)
        if btn:
            btn.setEnabled(True)
        self.serial_controller.on_save_file_done(target_path)

        # 启动后台线程生成PNG和XYZ（用于保存）
        self._start_silent_generate(target_path)

        # 如果勾选了自动成像，立即切换界面（会自动生成 latest_image.png）
        if hasattr(self, "save_aoto_gengrate_img") and self.save_aoto_gengrate_img.isChecked():
            self.log("[自动成像] 切换到成像界面...")
            self._auto_switch_and_image(target_path)
        else:
            self.log("[提示] 未勾选'保存后自动成像'，正在后台生成同名文件...")

    def _start_silent_generate(self, txt_file_path: str):
        """启动后台线程静默生成PNG和XYZ"""
        if hasattr(self, "_silent_generate_worker") and self._silent_generate_worker.isRunning():
            self.log("[警告] 上一次静默生成尚未完成")
            return

        base_name = os.path.splitext(os.path.basename(txt_file_path))[0]
        out_dir = self.lineEditOutputDir.text().strip() or get_output_dir()
        flip_horizontal = getattr(self, "_flip_horizontal", False)

        from core.imaging_pipeline import _SilentGenerateWorker
        self._silent_generate_worker = _SilentGenerateWorker(
            txt_file_path, out_dir, base_name, flip_horizontal
        )
        self._silent_generate_worker.finished.connect(self._on_silent_generate_done)
        self._silent_generate_worker.start()
        self.log(f"[静默生成] 开始后台生成: {base_name}")

    def _on_silent_generate_done(self, success: bool, png_path: str, xyz_path: str):
        """静默生成完成回调"""
        if success:
            if png_path:
                self.log(f"[静默生成] PNG已保存: {png_path}")
            if xyz_path:
                self._last_xyz_path = xyz_path
                self.log(f"[静默生成] XYZ已保存: {xyz_path}")
        else:
            self.log("[静默生成] 生成失败，请查看日志")

    def _on_serial_save_failed(self, target_path: str, error: str):
        btn = getattr(self, "SaveButton", None)
        if btn:
            btn.setEnabled(True)
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.critical(self, "保存失败", f"无法保存文件：\n{target_path}\n{error}")

    def _auto_switch_and_image(self, saved_file_path: str):
        """
        保存完成后，自动切换到成像界面并显示最新数据
        （生成固定名称的latest_image.png，因为这是最新数据）

        Args:
            saved_file_path: 实际保存的串口数据文件路径
        """
        try:
            self.log(f"[自动成像] 准备使用文件: {saved_file_path}")

            # 1. 切换到成像界面
            if hasattr(self, "pageImaging"):
                self._switch_to_page(self.pageImaging)
                self.log("[自动成像] 已切换到成像界面")

            # 2. 设置文件路径到成像界面的输入框
            if hasattr(self, "fileLineEdit"):
                self.fileLineEdit.setText(saved_file_path)
                self.log(f"[自动成像] 已设置数据文件路径: {saved_file_path}")

            # 3. 使用 QTimer 延迟触发2D成像，确保界面切换完成
            QtCore.QTimer.singleShot(200, self._auto_generate_2d_image)

        except Exception as e:
            self.log(f"[错误] 自动切换和成像失败: {e}")
            QMessageBox.warning(self, "警告", f"自动切换和成像过程出错：\n{e}")

    def _auto_generate_2d_image(self):
        """
        自动触发2D图像生成（生成固定名称latest_image.png）
        """
        try:
            self.log("[自动成像] 开始生成2D图像...")
            # 不传递base_name参数，使用默认的固定名称
            self.handle_image2d()
        except Exception as e:
            self.log(f"[错误] 自动生成2D图像失败: {e}")
            QMessageBox.warning(self, "警告", f"自动生成2D图像失败：\n{e}")

    # ---------- ScrollArea 重建 ----------
    def _rebuild_scroll_preview(self):
        """
        ScrollArea 直接 setWidget(ZoomableLabel)
        """
        if not hasattr(self, "scrollAreaImaging"):
            return

        sa = self.scrollAreaImaging

        sa.setWidgetResizable(False)
        sa.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        sa.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        sa.setAlignment(QtCore.Qt.AlignCenter)

        old_content = sa.widget()
        if old_content is not None:
            old_content.deleteLater()

        self.zoomLabel = ZoomableLabel()
        self.zoomLabel.setObjectName("labelImagePreview")
        self.zoomLabel.setStyleSheet(
            "background-color:#EAF4FB; "
            "border:1px solid #C6D9E6; "
            "border-radius:7px; "
            "color:#365766; "
            "font-weight:600;"
        )
        self.zoomLabel.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored,
            QtWidgets.QSizePolicy.Ignored
        )

        sa.setWidget(self.zoomLabel)
        self.labelImagePreview = self.zoomLabel
        self.zoomLabel.ensure_empty_canvas_size()

        sa.viewport().installEventFilter(self)

    # ---------- EventFilter：resize 自动 fit ----------
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Resize:
            if isinstance(getattr(self, "labelImagePreview", None), ZoomableLabel):
                if abs(self.labelImagePreview._scale - 1.0) < 1e-6:
                    self.labelImagePreview.fit_to_view()
                self.labelImagePreview.ensure_empty_canvas_size()
            for area_name, label_name in (
                ("scrollAreaPreview2D", "labelPreview2D"),
                ("scrollAreaPreview3D", "labelPreview3D"),
            ):
                area = getattr(self, area_name, None)
                label = getattr(self, label_name, None)
                if area is not None and obj is area.viewport() and isinstance(label, ZoomableLabel):
                    label.ensure_empty_canvas_size()
            return False

        # Shift+滚轮水平滚动支持
        if event.type() == QtCore.QEvent.Wheel:
            # 成像界面的 ScrollArea
            if hasattr(self, "scrollAreaImaging") and obj is self.scrollAreaImaging.viewport():
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    bar = self.scrollAreaImaging.horizontalScrollBar()
                    bar.setValue(bar.value() - event.angleDelta().y())
                    event.accept()
                    return True

            # 分割界面的 ScrollArea（2D视图）
            if hasattr(self, "scrollAreaPreview2D") and obj is self.scrollAreaPreview2D.viewport():
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    bar = self.scrollAreaPreview2D.horizontalScrollBar()
                    bar.setValue(bar.value() - event.angleDelta().y())
                    event.accept()
                    return True

            # 分割界面的 ScrollArea（3D热力图视图）
            if hasattr(self, "scrollAreaPreview3D") and obj is self.scrollAreaPreview3D.viewport():
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    bar = self.scrollAreaPreview3D.horizontalScrollBar()
                    bar.setValue(bar.value() - event.angleDelta().y())
                    event.accept()
                    return True

        return super().eventFilter(obj, event)

    # ---------- 默认图片加载 ----------
    def _load_default_images(self):
        """不再加载默认图片，保持预览区域为空"""
        # 成像预览区域保持空白，等待实际成像结果
        pass

    # ---------- CloudCompare 路径 ----------
    def _init_cloudcompare_path(self):
        """初始化CloudCompare路径（从配置读取）"""
        # 从配置文件读取
        default_cc = self.config.get("cloudcompare_path", r"C:\Program Files\CloudCompare\CloudCompare.exe")

        # 如果UI中有输入框，尝试使用UI中的值
        text = self.ccPathLineEdit.text().strip() if hasattr(self, "ccPathLineEdit") else ""

        if text and os.path.isfile(text) and text.lower().endswith(".exe"):
            self.cloudcompare_path = text
        else:
            self.cloudcompare_path = default_cc
            # 更新UI
            if hasattr(self, "ccPathLineEdit"):
                self.ccPathLineEdit.setText(default_cc)

        logger.info(f"[配置] CloudCompare路径: {self.cloudcompare_path}")

    # ---------- 顶部 logo ----------
    def _setup_header_logo(self, path: str):
        """顶部左上角 logo。"""
        if not hasattr(self, "labelLogo"):
            return

        full_path = resource_path(path) if not os.path.isabs(path) else path
        pix = QtGui.QPixmap(full_path)
        if pix.isNull():
            return

        label_height = self.labelLogo.height() or 40
        scaled = pix.scaledToHeight(label_height, QtCore.Qt.SmoothTransformation)
        self.labelLogo.setPixmap(scaled)
        self.labelLogo.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        self.labelLogo.setScaledContents(False)

    # ---------- 底部运行日志 ----------
    def log(self, text: str):
        if hasattr(self, "logTextEdit"):
            self.logTextEdit.appendPlainText(text)
        logger.info(text)

    # ---------- 阈值联动 ----------
    def _setup_segmentation_controls(self):
        if hasattr(self, "sliderThreshold") and hasattr(self, "spinThreshold"):
            self.sliderThreshold.setRange(0, 255)
            self.spinThreshold.setRange(0, 255)
            self.sliderThreshold.valueChanged.connect(self.spinThreshold.setValue)
            self.spinThreshold.valueChanged.connect(self.sliderThreshold.setValue)

    # ---------- 布局比例 ----------
    def _setup_layout_stretch(self):
        if hasattr(self, "horizontalLayout_imaging"):
            try:
                self.horizontalLayout_imaging.setStretch(0, 1)
                self.horizontalLayout_imaging.setStretch(1, 3)
            except TypeError:
                pass
        if hasattr(self, "horizontalLayout_seg"):
            try:
                # 分割分析界面：左侧控制栏占1份，右侧图像区占4份（左侧约20%）
                self.horizontalLayout_seg.setStretch(0, 1)
                self.horizontalLayout_seg.setStretch(1, 4)
            except TypeError:
                pass
        if hasattr(self, "horizontalLayout_machineRoot"):
            try:
                self.horizontalLayout_machineRoot.setStretch(0, 1)
                self.horizontalLayout_machineRoot.setStretch(1, 2)
            except TypeError:
                pass

    # ---------- 页面路由（4个页面） ----------
    def _setup_routing(self):
        """
        设置顶部四个 Tab 与 stackedMain 之间的切换关系：
        0: 机器校准
        1: 机器控制
        2: 成像界面
        3: 分割分析
        """
        # 默认打开：机器校准页
        if hasattr(self, "pageCalibration"):
            self.stackedMain.setCurrentWidget(self.pageCalibration)
            self._update_top_tab_style(self.pageCalibration)
            if hasattr(self, "labelBreadcrumb"):
                self.labelBreadcrumb.setText("机器校准 > 摄像头圆心检测与对准")
        elif hasattr(self, "pageMachineControl"):
            self.stackedMain.setCurrentWidget(self.pageMachineControl)
            self._update_top_tab_style(self.pageMachineControl)
            if hasattr(self, "labelBreadcrumb"):
                self.labelBreadcrumb.setText("机器控制 > 串口通讯与运动控制")
        else:
            if hasattr(self, "pageImaging"):
                self.stackedMain.setCurrentWidget(self.pageImaging)
                self._update_top_tab_style(self.pageImaging)
                if hasattr(self, "labelBreadcrumb"):
                    self.labelBreadcrumb.setText("成像 / 分割 > 管道内壁检测任务")

        # 顶部四个按钮切换页面
        if hasattr(self, "btnTopCalibration") and hasattr(self, "pageCalibration"):
            self.btnTopCalibration.clicked.connect(
                lambda: self._switch_to_page(self.pageCalibration)
            )
        if hasattr(self, "btnTopMachineControl") and hasattr(self, "pageMachineControl"):
            self.btnTopMachineControl.clicked.connect(
                lambda: self._switch_to_page(self.pageMachineControl)
            )
        if hasattr(self, "btnTopImaging") and hasattr(self, "pageImaging"):
            self.btnTopImaging.clicked.connect(
                lambda: self._switch_to_page(self.pageImaging)
            )
        if hasattr(self, "btnTopSegmentation") and hasattr(self, "pageSegmentation"):
            self.btnTopSegmentation.clicked.connect(
                lambda: self._switch_to_page(self.pageSegmentation)
            )

    def _switch_to_page(self, page: QtWidgets.QWidget):
        """切换 stackedMain 当前页面，并顺带更新顶部 Tab 和面包屑。"""
        self.stackedMain.setCurrentWidget(page)
        self._update_top_tab_style(page)

        if not hasattr(self, "labelBreadcrumb"):
            return

        if hasattr(self, "pageCalibration") and page is self.pageCalibration:
            self.labelBreadcrumb.setText("机器校准 > 摄像头圆心检测与对准")
        elif hasattr(self, "pageMachineControl") and page is self.pageMachineControl:
            self.labelBreadcrumb.setText("机器控制 > 串口通讯与运动控制")
        elif hasattr(self, "pageImaging") and page is self.pageImaging:
            self.labelBreadcrumb.setText("成像 / 分割 > 管道内壁检测任务")
        elif hasattr(self, "pageSegmentation") and page is self.pageSegmentation:
            self.labelBreadcrumb.setText("分割分析 > 内壁缺陷分割与统计")

    def _update_top_tab_style(self, current_page: QtWidgets.QWidget):
        """
        根据当前页面，高亮对应的顶部按钮。
        """
        active = ("background-color:#FFFFFF; color:#24558A; border:none;"
                  "border-radius:14px; min-height:28px; max-height:28px;"
                  "padding:0; font-weight:700;")
        inactive = ("background-color:transparent; color:#52647A; border:none;"
                    "border-radius:14px; min-height:28px; max-height:28px;"
                    "padding:0; font-weight:600;")

        # 先全部设为未激活
        if hasattr(self, "btnTopCalibration"):
            self.btnTopCalibration.setStyleSheet(inactive)
        if hasattr(self, "btnTopMachineControl"):
            self.btnTopMachineControl.setStyleSheet(inactive)
        if hasattr(self, "btnTopImaging"):
            self.btnTopImaging.setStyleSheet(inactive)
        if hasattr(self, "btnTopSegmentation"):
            self.btnTopSegmentation.setStyleSheet(inactive)

        # 再给当前页对应按钮设为 active
        if hasattr(self, "pageCalibration") and current_page is self.pageCalibration:
            self.btnTopCalibration.setStyleSheet(active)
        elif hasattr(self, "pageMachineControl") and current_page is self.pageMachineControl:
            self.btnTopMachineControl.setStyleSheet(active)
        elif hasattr(self, "pageImaging") and current_page is self.pageImaging:
            self.btnTopImaging.setStyleSheet(active)
        elif hasattr(self, "pageSegmentation") and current_page is self.pageSegmentation:
            self.btnTopSegmentation.setStyleSheet(active)

    # ================== 成像：文件选择 ==================
    def browse_file(self):
        filePath, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件", "", "Text Files (*.txt);;All Files (*)",
        )
        if filePath:
            self.fileLineEdit.setText(filePath)
            self.log(f"[INFO] 已选择数据文件：{filePath}")

    def choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", get_output_dir())
        if d:
            self.lineEditOutputDir.setText(d)
            self.log(f"[INFO] 输出目录设置为：{d}")

    # ================== 2D 成像 ==================
    # ================== 图像操作 ==================
    def flip_image_horizontal(self):
        """只做左右翻转，并记录当前翻转状态（用于 3D 点云）"""
        if self._current_qimage is None:
            QMessageBox.information(self, "提示", "当前没有可翻转的图像")
            return

        self._current_qimage = self._current_qimage.mirrored(True, False)
        pix = QtGui.QPixmap.fromImage(self._current_qimage)
        self._current_pixmap = pix

        self.labelImagePreview.setPixmap(pix)
        if isinstance(self.labelImagePreview, ZoomableLabel):
            self.labelImagePreview.fit_to_view()

        self._flip_horizontal = not self._flip_horizontal

    def save_current_image(self):
        if self._current_qimage is None:
            QMessageBox.information(self, "提示", "当前没有可保存的图像")
            return
        out_dir = self.lineEditOutputDir.text().strip() or get_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        # 保存图像
        image_path = os.path.join(out_dir, f"preview_saved_{timestamp}.png")
        self._current_qimage.save(image_path)
        logger.info(f"[预览保存] 保存图像: {image_path}")

        QMessageBox.information(self, "保存成功", f"已保存图像：\n{image_path}")

    def reset_preview(self):
        """
        关闭预览：清空图像显示区域
        """
        logger.info("[预览] 关闭预览，清空图像显示")
        self._current_qimage = None
        self._current_pixmap = None

        # 清空预览区域
        self.labelImagePreview.clear()
        if isinstance(self.labelImagePreview, ZoomableLabel):
            self.labelImagePreview.fit_to_view()

        logger.info("[预览] 预览已清空")

    # ================== 硬件监控 ==================
    def set_busy(self, busy: bool):
        """设置忙碌状态，自动调整硬件监控频率"""
        self._is_busy = bool(busy)
        if hasattr(self, "monitor_manager"):
            # 忙碌时降低监控频率（3秒），空闲时提高频率（1秒）
            interval = 3000 if self._is_busy else 1000
            self.monitor_manager.set_monitoring_interval(interval)

    # ---------- 监控相关方法（委托给 MonitorManager） ----------
    def _setup_hardware_monitoring(self):
        """设置硬件监控（由MonitorManager内部处理，此方法保留用于兼容性）"""
        pass

    def _update_hardware_ui(self, stats: dict):
        """更新硬件监控UI（委托给MonitorManager）"""
        self.monitor_manager.update_hardware_ui(stats)

    def update_server_status(self, is_online: bool, server_url: str = "", port: int = 8000):
        """更新服务器状态（委托给MonitorManager）"""
        self.monitor_manager.update_server_status(is_online, server_url, port)

    def _speed_widget(self, new_name: str, fallback_name: str):
        return getattr(self, new_name, None) or getattr(self, fallback_name, None)

    def _init_speed_display(self):
        if hasattr(self, "groupBox_hw"):
            self.groupBox_hw.setTitle("硬件监控 / 转速")

        title_latest = self._speed_widget("labelSpeedLatestTitle", "labelCPUTempTitle")
        title_avg = self._speed_widget("labelSpeedAvgTitle", "labelGPUTempTitle")
        if title_latest is not None:
            title_latest.setText("最新转速：")
        if title_avg is not None:
            title_avg.setText("平均转速：")
        self.update_speed_display({})

    def _on_speed_updated(self, summary: dict):
        self.update_speed_display(summary)

    def update_speed_display(self, summary: dict):
        latest = summary.get("latest_rpm") if summary else None
        avg = summary.get("avg_rpm") if summary else None
        count = int(summary.get("frame_count") or 0) if summary else 0
        min_rpm = summary.get("min_rpm") if summary else None
        max_rpm = summary.get("max_rpm") if summary else None

        latest_label = self._speed_widget("labelSpeedLatestValue", "labelCPUTempValue")
        avg_label = self._speed_widget("labelSpeedAvgValue", "labelGPUTempValue")

        latest_text = "-" if latest is None else f"{int(latest)} rpm"
        avg_text = "-" if avg is None else f"{avg:.0f} rpm ({count})"
        tip = "未收到转速数据"
        if count:
            tip = f"转速帧: {count}"
            if min_rpm is not None and max_rpm is not None:
                tip += f"\n范围: {int(min_rpm)} - {int(max_rpm)} rpm"

        if latest_label is not None:
            latest_label.setText(latest_text)
            latest_label.setToolTip(tip)
        if avg_label is not None:
            avg_label.setText(avg_text)
            avg_label.setToolTip(tip)

    # [电机控制] 串口控件信号连接与串口刷新
    def _init_motor_signals(self):
        if hasattr(self, "BT_chuankou_dakai"):
            self.BT_chuankou_dakai.clicked.connect(self._on_motor_open)
        if hasattr(self, "BT_chuankou_guanbi"):
            self.BT_chuankou_guanbi.clicked.connect(self._on_motor_close)
        if hasattr(self, "BT_chuankou_shuaxin"):
            self.BT_chuankou_shuaxin.clicked.connect(self._update_motor_ports)
        if hasattr(self, "BT_chuankou_tongxunzhongduan"):
            self.BT_chuankou_tongxunzhongduan.clicked.connect(self._on_motor_interrupt)
        self._motor_port_timer = QtCore.QTimer(self)
        self._motor_port_timer.timeout.connect(self._update_motor_ports)
        self._motor_port_timer.start(2000)
        self._update_motor_ports()

    def _update_motor_ports(self):
        combo = getattr(self, "CB_chuankou_xialakuang", None)
        if combo is None:
            return
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        ports = list(serial.tools.list_ports.comports())
        if ports:
            for p in ports:
                desc = f"{p.device} - {p.description}" if p.description else p.device
                combo.addItem(desc, p.device)
            combo.setEnabled(True)
            if current is not None:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        else:
            combo.addItem("未找到串口", None)
            combo.setEnabled(False)
        combo.blockSignals(False)

    def _set_motor_status(self, text: str):
        lbl = getattr(self, "LB_chuankou_lianjiezhuangtai", None)
        if lbl:
            lbl.setText(text)

    def _on_motor_open(self):
        combo = getattr(self, "CB_chuankou_xialakuang", None)
        port = combo.currentData() if combo else None
        if not port:
            QMessageBox.warning(self, "连接错误", "未找到可用串口")
            return
        if self._check_port_conflict(port):
            return
        if hasattr(self, "_motor_connect_worker") and self._motor_connect_worker.isRunning():
            return
        self._set_motor_status("连接中...")
        btn = getattr(self, "BT_chuankou_dakai", None)
        if btn:
            btn.setEnabled(False)

        self._motor_connect_worker = _MotorConnectWorker(self.motor_controller, port)
        self._motor_connect_worker.finished.connect(self._on_motor_connect_done)
        self._motor_connect_worker.start()

    def _on_motor_connect_done(self, ok: bool, err: str, port: str):
        btn = getattr(self, "BT_chuankou_dakai", None)
        if btn:
            btn.setEnabled(True)
        if ok:
            self._set_motor_status(f"已连接 {port}")
        else:
            self._set_motor_status("连接失败")
            QMessageBox.critical(self, "连接失败", err)

    def _on_motor_close(self):
        for attr in ("_motor_connect_worker", "_motor_interrupt_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                QMessageBox.warning(self, "警告", "电机操作进行中，请稍后再断开")
                return
        self.motor_controller.disconnect()
        self._set_motor_status("未连接")

    def _on_motor_interrupt(self):
        if not self.motor_controller.connected:
            QMessageBox.warning(self, "警告", "电机未连接")
            return
        if hasattr(self, "_motor_interrupt_worker") and self._motor_interrupt_worker.isRunning():
            return
        self.motor_controller._stop_flag.set()
        self._set_motor_status("发送中断中...")
        btn = getattr(self, "BT_chuankou_tongxunzhongduan", None)
        if btn:
            btn.setEnabled(False)
        self._motor_interrupt_worker = _MotorInterruptWorker(self.motor_controller)
        self._motor_interrupt_worker.finished.connect(self._on_motor_interrupt_done)
        self._motor_interrupt_worker.start()

    def _on_motor_interrupt_done(self, errors: list):
        btn = getattr(self, "BT_chuankou_tongxunzhongduan", None)
        if btn:
            btn.setEnabled(True)
        if errors:
            self.motor_controller._stop_flag.clear()
            QMessageBox.critical(self, "中断失败", "\n".join(errors))
        else:
            self.motor_controller._stop_flag.clear()
            port = self.motor_controller._port or ""
            self._set_motor_status(f"已连接 {port}")
            for btn_name in ("SendButton_2", "SendButton_3"):
                b = getattr(self, btn_name, None)
                if b:
                    b.setEnabled(True)
            if hasattr(self, "calib_controller"):
                self.calib_controller.cancel_motor_moves()

    # [探针控制] 信号连接
    def _init_tanzheng_signals(self):
        tc = self.tanzheng_controller
        combo = getattr(self, "CB_tanchengchuankou", None)
        if combo is not None:
            tc.bind_combo(combo)
        tc.status_changed.connect(self._set_tanzheng_status)

        if hasattr(self, "BT_tanzhengtongxunlianjie"):
            self.BT_tanzhengtongxunlianjie.clicked.connect(self._on_tanzheng_connect)
        if hasattr(self, "BT_tanzhengtongxunduankai"):
            self.BT_tanzhengtongxunduankai.clicked.connect(tc.disconnect_device)
        if hasattr(self, "BT_tanzhengxuanzhuan"):
            self.BT_tanzhengxuanzhuan.clicked.connect(self._on_tanzheng_rotate)
        if hasattr(self, "BT_tanzhengtingzhi"):
            self.BT_tanzhengtingzhi.clicked.connect(self._on_tanzheng_stop)

    def _set_tanzheng_status(self, text: str):
        lbl = getattr(self, "LB_tanzhengzhuangtai", None)
        if lbl:
            lbl.setText(text)

    def _check_port_conflict(self, port: str) -> bool:
        """检查端口是否已被其他控制器占用，返回 True 表示有冲突。"""
        p = port.upper()
        # 单片机串口
        if self.serial_controller.is_open():
            sp = self.serial_controller.serial.portName() if self.serial_controller.serial else None
            if sp and sp.upper() == p:
                QMessageBox.warning(self, "串口冲突", f"端口 {port} 已被单片机串口占用")
                return True
        # 电机控制串口
        if self.motor_controller.connected and self.motor_controller._port:
            if self.motor_controller._port.upper() == p:
                QMessageBox.warning(self, "串口冲突", f"端口 {port} 已被电机控制器占用")
                return True
        # 探针串口
        if self.tanzheng_controller.connected and self.tanzheng_controller._port:
            if self.tanzheng_controller._port.upper() == p:
                QMessageBox.warning(self, "串口冲突", f"端口 {port} 已被探针控制器占用")
                return True
        return False

    def _on_tanzheng_connect(self):
        combo = getattr(self, "CB_tanchengchuankou", None)
        port = combo.currentData() if combo else None
        if not port:
            QMessageBox.warning(self, "连接错误", "未找到可用串口")
            return
        if self._check_port_conflict(port):
            return
        btn = getattr(self, "BT_tanzhengtongxunlianjie", None)
        if btn:
            btn.setEnabled(False)
        self._set_tanzheng_status("连接中...")
        self._tanzheng_connect_worker = _TanzhengConnectWorker(self.tanzheng_controller, port)
        self._tanzheng_connect_worker.finished.connect(self._on_tanzheng_connect_done)
        self._tanzheng_connect_worker.start()

    def _on_tanzheng_connect_done(self, connected: bool, port: str):
        btn = getattr(self, "BT_tanzhengtongxunlianjie", None)
        if btn:
            btn.setEnabled(True)
        if not connected:
            QMessageBox.critical(self, "连接失败", f"无法连接到 {port}")

    def _on_tanzheng_rotate(self):
        if not self.tanzheng_controller.connected:
            QMessageBox.warning(self, "警告", "探针控制器未连接")
            return
        if hasattr(self, "_tanzheng_write_worker") and self._tanzheng_write_worker.isRunning():
            return
        self._tanzheng_write_worker = _TanzhengWriteWorker(self.tanzheng_controller, "rotate")
        self._tanzheng_write_worker.start()

    def _on_tanzheng_stop(self):
        if not self.tanzheng_controller.connected:
            QMessageBox.warning(self, "警告", "探针控制器未连接")
            return
        if hasattr(self, "_tanzheng_write_worker") and self._tanzheng_write_worker.isRunning():
            return
        self._tanzheng_write_worker = _TanzhengWriteWorker(self.tanzheng_controller, "stop")
        self._tanzheng_write_worker.start()

    def closeEvent(self, event):
        # 0. 检查是否需要电机复位
        if hasattr(self, "motor_controller") and self.motor_controller.connected:
            if hasattr(self, "_current_z_pos") and self._current_z_pos != 0:
                try:
                    displacement = -self._current_z_pos
                    dist_mm = abs(displacement) / 90
                    logger.info(f"[应用关闭] 检测到电机未复位，正在执行自动复位: {dist_mm:.2f}mm")
                    
                    # 停止当前可能正在运行的 Z 轴线程
                    if hasattr(self, "_z_move_worker") and self._z_move_worker.isRunning():
                        self._z_move_worker.terminate()
                        self._z_move_worker.wait()
                    
                    # 停止测量流程线程
                    if hasattr(self, "imaging_pipeline") and hasattr(self.imaging_pipeline, "_measure_worker"):
                        if self.imaging_pipeline._measure_worker.isRunning():
                            self.imaging_pipeline._measure_worker.terminate()
                            self.imaging_pipeline._measure_worker.wait()

                    # 同步执行复位（阻塞主线程直至完成或超时）
                    ok, err = self.motor_controller.move_motor_and_wait(
                        slave_id=3,
                        displacement=displacement,
                        timeout=60.0
                    )
                    if ok:
                        logger.info("[应用关闭] 电机自动复位成功")
                        self._current_z_pos = 0
                    else:
                        logger.error(f"[应用关闭] 电机自动复位失败: {err}")
                except Exception as e:
                    logger.error(f"[应用关闭] 执行自动复位时发生异常: {e}")

        # 1. 保存当前配置
        try:
            # 保存CloudCompare路径
            if hasattr(self, "cloudcompare_path"):
                self.config.set("cloudcompare_path", self.cloudcompare_path)

            # 保存配置到文件
            self.config.save()
            logger.info("[配置] 配置已保存")
        except Exception as e:
            logger.error(f"[配置] 保存配置失败: {e}")

        # 停止硬件监控线程
        if hasattr(self, "monitor_manager"):
            self.monitor_manager.stop()

        # 停止串口扫描定时器，防止关闭后继续访问已销毁的控件
        if hasattr(self, "_motor_port_timer"):
            self._motor_port_timer.stop()

        # 停止探针串口扫描定时器
        if hasattr(self, "tanzheng_controller"):
            self.tanzheng_controller._port_timer.stop()

        # 等待所有后台 worker 线程结束，防止关闭时访问已销毁对象
        for attr in ("_motor_connect_worker", "_motor_interrupt_worker",
                     "_tanzheng_connect_worker", "_tanzheng_write_worker",
                     "_serial_save_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.wait(3000)

        if hasattr(self, "imaging_pipeline"):
            for attr in ("_3d_worker", "_2d_worker", "_silent_gen_worker"):
                worker = getattr(self.imaging_pipeline, attr, None)
                if worker is not None and worker.isRunning():
                    worker.wait(3000)

        # 断开 Modbus 连接
        if hasattr(self, "motor_controller"):
            self.motor_controller.disconnect()
        if hasattr(self, "tanzheng_controller"):
            self.tanzheng_controller.disconnect_device()

        # 释放摄像头资源
        if hasattr(self, "calib_controller"):
            self.calib_controller.cleanup()

        # 关闭串口
        if hasattr(self, "serial_controller") and self.serial_controller.is_open():
            self.serial_controller.close_serial()
            logger.info("[串口] 串口已关闭")

        logger.info("[应用] 应用已关闭")
        super().closeEvent(event)
