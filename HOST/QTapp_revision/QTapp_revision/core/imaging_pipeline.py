# -*- coding: utf-8 -*-
"""
成像管道模块

职责：
1. 发送测量命令
2. 读取和预处理图像数据
3. 2D成像处理
4. 3D成像处理
5. 自动成像流程

从 MainWindow 中提取，降低主窗口复杂度
"""

import os
import time
import subprocess
import logging
import json
from typing import Optional

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QMessageBox

from algorithms.cylinder_3d import run_pipeline_3d
from ui.widgets.zoomable_label import ZoomableLabel
from utils.speed_frame_filter import strip_speed_frames, summarize_speed_frames


logger = logging.getLogger(__name__)


def _median_blur_uint8(image: np.ndarray, size: int) -> np.ndarray:
    ksize = int(size)
    if ksize <= 1:
        return image
    if ksize % 2 == 0:
        ksize += 1
    return cv2.medianBlur(image, ksize)


def _resize_gray_image(image: np.ndarray, width: int, height: int, interpolation) -> np.ndarray:
    if image.shape[1] == width and image.shape[0] == height:
        return image
    return cv2.resize(image, (width, height), interpolation=interpolation)


def _gaussian_smooth(data: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return data.copy()
    source = np.asarray(data)
    if source.ndim == 1:
        smoothed = cv2.GaussianBlur(source.reshape(1, -1), (0, 0), sigmaX=sigma)
        return smoothed.reshape(source.shape)
    return cv2.GaussianBlur(source, (0, 0), sigmaX=sigma, sigmaY=sigma)


class _SilentGenerateWorker(QtCore.QThread):
    """后台静默生成PNG和XYZ文件，避免阻塞UI"""
    finished = QtCore.pyqtSignal(bool, str, str)  # success, png_path, xyz_path

    def __init__(self, txt_file_path, out_dir, base_name, flip_horizontal=False):
        super().__init__()
        self._txt_file_path = txt_file_path
        self._out_dir = out_dir
        self._base_name = base_name
        self._flip_horizontal = flip_horizontal
        self._png_path = ""
        self._xyz_path = ""

    def run(self):
        try:
            os.makedirs(self._out_dir, exist_ok=True)

            # 1. 生成PNG图像
            try:
                raw = _read_hex_file_static(self._txt_file_path)
                if raw is not None and len(raw) > 0:
                    data_uint8 = _preprocess_to_uint8_static(raw)

                    width = 4000
                    total = len(data_uint8)
                    height = (total + width - 1) // width
                    padded_size = width * height

                    if total < padded_size:
                        data_uint8 = np.pad(data_uint8, (0, padded_size - total),
                                            mode='edge')

                    img_data = data_uint8.reshape((height, width))
                    qimg = QtGui.QImage(
                        img_data.data, width, height, width,
                        QtGui.QImage.Format_Grayscale8
                    ).copy()

                    self._png_path = os.path.join(self._out_dir, f"{self._base_name}.png")
                    qimg.save(self._png_path)
                    logger.info(f"[静默生成] PNG: {self._png_path}")
            except Exception as e:
                logger.error(f"[静默生成] PNG失败: {e}")

            # 2. 生成XYZ点云
            try:
                self._xyz_path = os.path.join(self._out_dir, f"{self._base_name}.xyz")
                run_pipeline_3d(
                    file_path=self._txt_file_path,
                    width=4000,
                    radius_inner=1.0,
                    z_min=0.0,
                    xyz_path=self._xyz_path,
                    isotropic_spacing=True,
                    radius_min_ratio=0.8,
                    flip_horizontal=self._flip_horizontal
                )
                logger.info(f"[静默生成] XYZ: {self._xyz_path}")
            except Exception as e:
                logger.error(f"[静默生成] XYZ失败: {e}")

            self.finished.emit(True, self._png_path, self._xyz_path)

        except Exception as e:
            logger.error(f"[静默生成] 失败: {e}")
            self.finished.emit(False, "", "")



_Z_AXIS_SPEED_MM_PER_S = 13.33  # 实测电机下降距离与时间的关系：100mm / 7.5s


class _MeasureSequenceWorker(QtCore.QThread):
    """后台执行：开采集 + Z轴下降等待到位 + 停采集 + Z轴上升"""
    finished = QtCore.pyqtSignal(bool, str)
    request_serial_write = QtCore.pyqtSignal(bytes)  # 请求主线程写串口

    def __init__(self, serial_controller, motor_controller, displacement_down: int,
                 displacement_up: int, timeout: float = 15.0):
        super().__init__()
        self._serial = serial_controller
        self._motor = motor_controller
        self._disp_down = displacement_down
        self._disp_up = displacement_up
        self._timeout = timeout
        # 根据距离和速度计算采集时间，避免底部停顿时重复采集
        self._acq_time = abs(displacement_down) / 90 / _Z_AXIS_SPEED_MM_PER_S

    def run(self):
        import threading
        _stopped = threading.Event()
        _timer = [None]

        def _stop_acquisition():
            if _stopped.is_set():
                return
            _stopped.set()
            self._serial.write_data(b"C\x00")

        def _start_acquisition():
            self._serial.write_data(b"O\x00")
            # 定时停止采集，电机128信号到来时采集已结束
            _timer[0] = threading.Timer(self._acq_time, _stop_acquisition)
            _timer[0].start()

        def _on_motor_done():
            # 电机到位时取消定时器（若还未触发），确保采集已停止
            if _timer[0] is not None:
                _timer[0].cancel()
            _stop_acquisition()

        ok, err = self._motor.move_motor_and_wait(
            slave_id=3, displacement=self._disp_down, timeout=self._timeout,
            on_start=_start_acquisition, on_done=_on_motor_done
        )
        if not ok:
            if _timer[0] is not None:
                _timer[0].cancel()
            _stop_acquisition()
            self.finished.emit(False, f"Z轴下降失败: {err}")
            return

        # 4. Z轴上升回初始位置
        ok, err = self._motor.move_motor_and_wait(
            slave_id=3, displacement=self._disp_up, timeout=self._timeout
        )
        self.finished.emit(ok, err if not ok else "")


class _Image3DWorker(QtCore.QThread):
    """在子线程中执行耗时的3D点云生成和CloudCompare转换，避免阻塞UI主线程。"""
    succeeded = QtCore.pyqtSignal(str, str)   # xyz_path, bin_path
    failed = QtCore.pyqtSignal(str)           # error message
    def __init__(self, file_path, out_dir, xyz_path, bin_path, cloudcompare_path,
                 flip_horizontal, ds_ratio=1.0, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._out_dir = out_dir
        self._xyz_path = xyz_path
        self._bin_path = bin_path
        self._cloudcompare_path = cloudcompare_path
        self._flip_horizontal = flip_horizontal
        self._ds_ratio = ds_ratio

    def run(self):
        try:
            run_pipeline_3d(
                file_path=self._file_path,
                width=4000,
                xyz_path=self._xyz_path,
                radius_inner=1.0,
                z_min=0.0,
                isotropic_spacing=True,
                radius_min_ratio=0.8,
                flip_horizontal=self._flip_horizontal,
                ds_ratio=self._ds_ratio,
            )

            cmd = [
                self._cloudcompare_path,
                "-SILENT", "-NO_TIMESTAMP",
                "-C_EXPORT_FMT", "BIN",
                "-O", self._xyz_path,
                "-SAVE_CLOUDS", "FILE", self._bin_path,
            ]
            subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

            if not os.path.exists(self._bin_path):
                self.failed.emit(f"BIN 未生成：\n{self._bin_path}")
                return

            self.succeeded.emit(self._xyz_path, self._bin_path)

        except subprocess.CalledProcessError as e:
            self.failed.emit(f"CloudCompare 执行失败：\n{e}")
        except Exception as e:
            self.failed.emit(str(e))


class _Image2DWorker(QtCore.QThread):
    """在子线程中执行2D图像生成的耗时计算，避免阻塞UI主线程。"""
    succeeded = QtCore.pyqtSignal(object, str, object)  # QImage, out_path, speed_summary
    failed = QtCore.pyqtSignal(str)

    def __init__(self, file_path, out_dir, base_name,
                 low_pct, high_pct, factor, width=4000, ds_ratio=1.0, upsample_factor=1, denoise_size=0, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._out_dir = out_dir
        self._base_name = base_name
        self._low_pct = low_pct
        self._high_pct = high_pct
        self._factor = factor
        self._width = width
        self._ds_ratio = ds_ratio
        self._upsample_factor = upsample_factor
        self._denoise_size = denoise_size

    def run(self):
        try:
            raw, speed_summary = _read_hex_file_with_speed_static(self._file_path)
            if raw.size == 0:
                self.failed.emit("没有读取到有效数据")
                return

            data_uint8 = _preprocess_to_uint8_static(raw)
            data_norm = data_uint8.astype(np.float64) / 255.0
            # data_norm = gaussian_filter(data_norm, sigma=3)
            p_low = np.percentile(data_norm, self._low_pct)
            p_high = np.percentile(data_norm, self._high_pct)
            out = data_norm.copy()
            mask = (data_norm >= p_low) & (data_norm <= p_high)
            out[mask] = p_low + (data_norm[mask] - p_low) * self._factor
            data_norm = np.clip(out, 0.0, 1.0)
            data_uint8 = (data_norm * 255).astype(np.uint8)

            width = self._width
            remainder = data_uint8.size % width
            if remainder != 0:
                data_uint8 = np.pad(data_uint8, (0, width - remainder), mode='edge')
            height = data_uint8.size // width
            img2d = data_uint8.reshape((height, width))

            if self._denoise_size > 0:
                img2d = _median_blur_uint8(img2d, self._denoise_size)

            if self._ds_ratio < 1.0:
                target_w = max(1, round(width * self._ds_ratio))
                img2d = _resize_gray_image(img2d, target_w, height, cv2.INTER_LINEAR)
                width = target_w
                # 双三次插值等比放大，补偿降采样损失的像素
                if self._upsample_factor > 1:
                    target_w = width * self._upsample_factor
                    target_h = height * self._upsample_factor
                    img2d = _resize_gray_image(img2d, target_w, target_h, cv2.INTER_CUBIC)
                    width = img2d.shape[1]
                    height = img2d.shape[0]

            qimg = QtGui.QImage(
                img2d.data, width, height, width,
                QtGui.QImage.Format_Grayscale8
            ).copy()

            os.makedirs(self._out_dir, exist_ok=True)
            if self._base_name:
                out_path = os.path.join(self._out_dir, f"{self._base_name}.png")
            else:
                out_path = os.path.join(self._out_dir, "latest_image.png")
            qimg.save(out_path)
            _write_speed_meta(out_path, self._file_path, speed_summary)
            self.succeeded.emit(qimg, out_path, speed_summary)
        except Exception as e:
            self.failed.emit(str(e))


class _SilentGenWorker(QtCore.QThread):
    """在子线程中静默生成PNG和XYZ文件，避免阻塞UI主线程。"""
    finished = QtCore.pyqtSignal()

    def __init__(self, txt_file_path, out_dir, parent=None):
        super().__init__(parent)
        self._txt_file_path = txt_file_path
        self._out_dir = out_dir

    def run(self):
        try:
            base_name = os.path.splitext(os.path.basename(self._txt_file_path))[0]
            os.makedirs(self._out_dir, exist_ok=True)

            try:
                raw = _read_hex_file_static(self._txt_file_path)
                if raw is not None and len(raw) > 0:
                    data_uint8 = _preprocess_to_uint8_static(raw)
                    width = 4000
                    total = len(data_uint8)
                    height = (total + width - 1) // width
                    padded_size = width * height
                    if total < padded_size:
                        data_uint8 = np.pad(data_uint8, (0, padded_size - total),
                                            mode='edge')
                    img_data = data_uint8.reshape((height, width))
                    qimg = QtGui.QImage(
                        img_data.data, width, height, width,
                        QtGui.QImage.Format_Grayscale8
                    ).copy()
                    png_path = os.path.join(self._out_dir, f"{base_name}.png")
                    qimg.save(png_path)
            except Exception as e:
                logger.error(f"[静默生成] PNG生成失败: {e}")

            try:
                xyz_path = os.path.join(self._out_dir, f"{base_name}.xyz")
                run_pipeline_3d(
                    file_path=self._txt_file_path,
                    width=4000,
                    radius_inner=1.0,
                    z_min=0.0,
                    xyz_path=xyz_path,
                    isotropic_spacing=True,
                    radius_min_ratio=0.8,
                    flip_horizontal=False,
                )
            except Exception as e:
                logger.error(f"[静默生成] XYZ生成失败: {e}")

        except Exception as e:
            logger.error(f"[静默生成] 整体流程失败: {e}")
        finally:
            self.finished.emit()


def _read_hex_file_with_speed_static(file_path: str) -> tuple[np.ndarray, dict]:
    """Read saved hex data, strip speed frames, and return RPM metadata."""
    values = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                for token in line.split():
                    try:
                        values.append(int(token, 16))
                    except ValueError:
                        pass
    except Exception as e:
        logger.error(f"[读取] 文件读取失败: {e}")
    values, speed_frames = strip_speed_frames(values)
    speed_summary = summarize_speed_frames(speed_frames)
    speed_summary["source"] = "file"
    if speed_frames:
        logger.info(
            "[read] removed %d speed frame(s) before imaging, first rpm=%d",
            len(speed_frames),
            speed_frames[0]["rpm"],
        )
    return np.array(values, dtype=np.uint16), speed_summary


def _read_hex_file_static(file_path: str) -> np.ndarray:
    """模块级静态函数，供 worker 线程调用，不依赖 self。"""
    values, _ = _read_hex_file_with_speed_static(file_path)
    return values


def _write_speed_meta(image_path: str, source_path: str, speed_summary: dict):
    if not speed_summary or not speed_summary.get("frame_count"):
        return
    meta_path = os.path.splitext(image_path)[0] + ".meta.json"
    payload = {
        "source_file": source_path,
        "image_file": image_path,
        "speed": speed_summary,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _preprocess_to_uint8_static(x: np.ndarray) -> np.ndarray:
    """模块级静态函数，供 worker 线程调用。"""
    if x.size == 0:
        return np.zeros((0,), dtype=np.uint8)
    mn, mx = float(x.min()), float(x.max())
    if mx > mn:
        x = (x - mn) / (mx - mn)
    else:
        x = np.zeros_like(x, dtype=np.float64)
    return (np.clip(x, 0.0, 1.0) * 255).astype(np.uint8)


class ImagingPipeline:
    """
    成像管道：负责2D/3D成像处理的所有操作

    该类封装了图像数据读取、预处理、2D成像、3D成像、自动成像流程等功能。
    从 MainWindow 中提取，实现关注点分离。
    """

    def __init__(self, parent, ui, config, serial_controller, get_app_dir_func, get_output_dir_func):
        """
        初始化成像管道

        Args:
            parent: 父窗口对象（用于显示消息框和访问方法）
            ui: UI对象，包含所有UI控件的引用
            config: 配置管理器对象
            serial_controller: 串口控制器对象
            get_app_dir_func: 获取应用目录的函数
            get_output_dir_func: 获取输出目录的函数
        """
        self.parent = parent
        self.ui = ui
        self.config = config
        self.serial_controller = serial_controller
        self.get_app_dir = get_app_dir_func
        self.get_output_dir = get_output_dir_func

        # 初始化worker为None，防止hasattr检查时多个worker并发运行
        self._2d_worker: Optional[_Image2DWorker] = None
        self._3d_worker: Optional[_Image3DWorker] = None
        self._silent_gen_worker: Optional[_SilentGenWorker] = None

        logger.info("[成像管道] 初始化完成")

    def send_measure_command(self) -> bool:
        if not self.serial_controller.ensure_open():
            return False

        motor = getattr(self.parent, "motor_controller", None)
        if motor is None or not motor.connected:
            QMessageBox.warning(self.parent, "警告", "请先连接电机串口")
            return False

        if hasattr(self, "_measure_worker") and self._measure_worker.isRunning():
            QMessageBox.warning(self.parent, "提示", "测量流程正在进行中")
            return False

        M_str = self.ui.MLineEdit.text().strip() if hasattr(self.ui, "MLineEdit") else ""
        if not M_str:
            QMessageBox.warning(self.parent, "警告", "请填写移动距离 M")
            return False

        try:
            disp_down = -int(float(M_str)) * 90
            disp_up = int(float(M_str)) * 90
        except ValueError:
            QMessageBox.warning(self.parent, "警告", "距离值必须为数字")
            return False

        send_btn = getattr(self.ui, "SendButton", None)
        if send_btn:
            send_btn.setEnabled(False)

        self._measure_worker = _MeasureSequenceWorker(
            self.serial_controller, motor, disp_down, disp_up
        )
        self._measure_worker.request_serial_write.connect(
            lambda data: self.serial_controller.write_data(data),
            QtCore.Qt.QueuedConnection
        )
        self._measure_worker.finished.connect(
            lambda ok, err: self._on_measure_done(ok, err, send_btn),
            QtCore.Qt.QueuedConnection
        )
        self._measure_worker.start()
        self._log(f"[测量] 开始：下降={M_str}mm，上升={M_str}mm")
        return True

    def _on_measure_done(self, ok: bool, err: str, send_btn):
        if send_btn:
            send_btn.setEnabled(True)
        if ok:
            self._log("[测量] 完成：采集结束，Z轴已回位")
        else:
            self._log(f"[测量] 失败：{err}")
            QMessageBox.warning(self.parent, "测量失败", err)

    def read_hex_file(self, file_path: str) -> np.ndarray:
        """
        读取HEX格式的数据文件

        Args:
            file_path: 文件路径

        Returns:
            np.ndarray: 读取的数据数组
        """
        return _read_hex_file_static(file_path).astype(np.float64)

    def smooth_image(self, data, sigma=1):
        """
        使用高斯滤波去噪和平滑图像。

        参数:
            data (numpy.ndarray): 输入的图像数据（已归一化）
            sigma (float): 高斯滤波的标准差，控制平滑的程度（默认1）

        返回:
            data_smoothed (numpy.ndarray): 去噪和平滑后的图像数据
        """
        # 高斯滤波去噪
        data_smoothed = _gaussian_smooth(data, sigma=sigma)
        return data_smoothed

    def enhance_contrast(self, data: np.ndarray, low_pct: float = 45, high_pct: float = 99, factor: float = 3.5) -> np.ndarray:
        """对比度增强：对百分位区间做线性拉伸"""
        p_low = np.percentile(data, low_pct)
        p_high = np.percentile(data, high_pct)
        out = data.copy()
        mask = (data >= p_low) & (data <= p_high)
        out[mask] = p_low + (data[mask] - p_low) * factor
        return np.clip(out, 0.0, 1.0)

    def preprocess_to_uint8(self, x: np.ndarray) -> np.ndarray:
        """
        预处理图像数据到uint8格式

        将数据归一化到[0, 255]范围

        Args:
            x: 输入数据数组

        Returns:
            np.ndarray: uint8格式的数据数组
        """
        if x.size == 0:
            return np.zeros((0,), dtype=np.uint8)

        mn, mx = float(x.min()), float(x.max())
        if mx > mn:
            x = (x - mn) / (mx - mn)
        else:
            x = np.zeros_like(x)

        x = np.clip(x, 0.0, 1.0)
        return (x * 255).astype(np.uint8)

    def handle_image_2d(self, base_name=None):
        """处理2D成像：在子线程中执行耗时计算，避免阻塞UI主线程。"""
        if self._2d_worker is not None and self._2d_worker.isRunning():
            return
        file_path = self.ui.fileLineEdit.text().strip()
        if not file_path:
            QMessageBox.warning(self.parent, "警告", "请先选择数据文件！")
            return
        if not os.path.exists(file_path):
            QMessageBox.warning(self.parent, "警告", f"文件不存在:\n{file_path}")
            return
        if not os.access(file_path, os.R_OK):
            QMessageBox.warning(self.parent, "警告", f"文件无法读取（可能被占用）:\n{file_path}")
            return

        out_dir = self.ui.lineEditOutputDir.text().strip() or self.get_output_dir()
        low_pct = float(self.ui.dizhi.text()) if hasattr(self.ui, "dizhi") and self.ui.dizhi.text() else 45
        high_pct = float(self.ui.gaozhi.text()) if hasattr(self.ui, "gaozhi") and self.ui.gaozhi.text() else 99
        factor = float(self.ui.beishuzhi.text()) if hasattr(self.ui, "beishuzhi") and self.ui.beishuzhi.text() else 3.5
        image_width = self.config.get("imaging.width", 4000)
        ds_ratio = float(self.ui.dszhi.text()) if hasattr(self.ui, "dszhi") and self.ui.dszhi.text() else 1.0

        self._set_busy(True)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)

        try:
            self._2d_worker = _Image2DWorker(file_path, out_dir, base_name, low_pct, high_pct, factor, image_width, ds_ratio,
                                                upsample_factor=self.config.get("imaging.upsample_factor", 4),
                                                denoise_size=self.config.get("imaging.denoise_size", 0))
            self._2d_worker.succeeded.connect(self._on_2d_worker_succeeded)
            self._2d_worker.failed.connect(self._on_2d_worker_failed)
            self._2d_worker.start()
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._set_busy(False)
            QMessageBox.critical(self.parent, "错误", f"启动2D成像失败：{e}")

    def _on_2d_worker_succeeded(self, qimg, out_path: str, speed_summary=None):
        QtWidgets.QApplication.restoreOverrideCursor()
        self._set_busy(False)
        self._log(f"[2D] 图像已保存：{out_path}")

        pix = QtGui.QPixmap.fromImage(qimg)
        self.parent._current_qimage = qimg
        self.parent._current_pixmap = pix
        if hasattr(self.ui, "labelImagePreview"):
            self.ui.labelImagePreview.setPixmap(pix)
            if isinstance(self.ui.labelImagePreview, ZoomableLabel):
                self.ui.labelImagePreview.fit_to_view()
        self.parent._flip_horizontal = False
        if hasattr(self.parent, "update_speed_display"):
            self.parent.update_speed_display(speed_summary or {})
        QMessageBox.information(self.parent, "成功", f"2D 图像生成成功！\n保存位置：\n{out_path}")

    def _on_2d_worker_failed(self, error: str):
        QtWidgets.QApplication.restoreOverrideCursor()
        self._set_busy(False)
        QMessageBox.critical(self.parent, "错误", error)

    def handle_image_3d(self, use_timestamp=False):
        """处理3D成像：在子线程中执行耗时的点云生成和CloudCompare转换。"""
        file_path = self.ui.fileLineEdit.text().strip()
        if not file_path:
            QMessageBox.warning(self.parent, "警告", "请先选择数据文件！")
            return
        if not os.path.exists(file_path):
            QMessageBox.warning(self.parent, "警告", f"数据文件不存在：\n{file_path}")
            return

        cloudcompare_path = getattr(self.parent, "cloudcompare_path", "")
        if not os.path.exists(cloudcompare_path):
            QMessageBox.warning(self.parent, "警告", "CloudCompare 路径无效，请检查 ccPathLineEdit。")
            return

        out_dir = self.ui.lineEditOutputDir.text().strip() or self.get_output_dir()
        os.makedirs(out_dir, exist_ok=True)

        if use_timestamp:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            xyz_path = os.path.join(out_dir, f"cylinder_points_{timestamp}.xyz")
        else:
            xyz_path = os.path.join(out_dir, "latest_points.xyz")
        bin_path = os.path.splitext(xyz_path)[0] + ".bin"

        self._log(f"[3D] 输出目录: {out_dir}")
        logger.info(f"[3D成像] 生成点云: {xyz_path}")

        self._set_busy(True)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)

        try:
            self._3d_worker = _Image3DWorker(
                file_path=file_path,
                out_dir=out_dir,
                xyz_path=xyz_path,
                bin_path=bin_path,
                cloudcompare_path=cloudcompare_path,
                flip_horizontal=getattr(self.parent, "_flip_horizontal", False),
                ds_ratio=float(self.ui.dszhi.text()) if hasattr(self.ui, "dszhi") and self.ui.dszhi.text() else 1.0,
            )
            self._3d_worker.succeeded.connect(self._on_3d_worker_succeeded)
            self._3d_worker.failed.connect(self._on_3d_worker_failed)
            self._3d_worker.start()
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._set_busy(False)
            QMessageBox.critical(self.parent, "错误", f"启动3D成像失败：{e}")

    def _on_3d_worker_succeeded(self, xyz_path: str, bin_path: str):
        QtWidgets.QApplication.restoreOverrideCursor()
        self._set_busy(False)

        self.parent._last_xyz_path = xyz_path
        self._log("[3D] 打开 BIN")
        cloudcompare_path = getattr(self.parent, "cloudcompare_path", "")
        subprocess.Popen([cloudcompare_path, bin_path], creationflags=subprocess.CREATE_NO_WINDOW)

        QMessageBox.information(self.parent, "成功", f"3D 点云已生成并打开：\n{bin_path}")

        if hasattr(self.ui, "tabWidgetImagingView") and hasattr(self.parent, "pyvista_viewer"):
            current_index = self.ui.tabWidgetImagingView.currentIndex()
            current_tab_text = self.ui.tabWidgetImagingView.tabText(current_index)
            if ("3D" in current_tab_text or "点云" in current_tab_text) and self.parent.pyvista_viewer is not None:
                QtCore.QTimer.singleShot(500, self.parent._load_pyvista_data)
                self._log("[PyVista] 将在 500ms 后刷新 3D 窗口显示")

    def _on_3d_worker_failed(self, error: str):
        QtWidgets.QApplication.restoreOverrideCursor()
        self._set_busy(False)
        QMessageBox.critical(self.parent, "错误", error)

    def generate_image_and_xyz_silently(self, txt_file_path: str):
        """静默生成PNG和XYZ文件，在子线程中执行，不阻塞UI主线程。"""
        out_dir = self.ui.lineEditOutputDir.text().strip() or self.get_output_dir()
        self._log(f"[静默生成] 开始: {os.path.basename(txt_file_path)}")
        self._silent_gen_worker = _SilentGenWorker(txt_file_path, out_dir)
        self._silent_gen_worker.finished.connect(
            lambda: self._log("[静默生成] 完成")
        )
        self._silent_gen_worker.start()

    def auto_generate_2d_image(self):
        """
        自动触发2D图像生成（生成固定名称latest_image.png）
        """
        try:
            self._log("[自动成像] 开始生成2D图像...")
            # 不传递base_name参数，使用默认的固定名称
            self.handle_image_2d()
        except Exception as e:
            self._log(f"[错误] 自动生成2D图像失败: {e}")
            QMessageBox.warning(self.parent, "警告", f"自动生成2D图像失败：\n{e}")

    def auto_switch_and_image(self, saved_file_path: str):
        """
        保存完成后，自动切换到成像界面并显示最新数据
        （生成固定名称的latest_image.png，因为这是最新数据）

        Args:
            saved_file_path: 实际保存的串口数据文件路径
        """
        try:
            self._log(f"[自动成像] 准备使用文件: {saved_file_path}")

            # 1. 切换到成像界面
            if hasattr(self.parent, "pageImaging"):
                self.parent._switch_to_page(self.parent.pageImaging)
                self._log("[自动成像] 已切换到成像界面")

            # 2. 设置文件路径到成像界面的输入框
            if hasattr(self.ui, "fileLineEdit"):
                self.ui.fileLineEdit.setText(saved_file_path)
                self._log(f"[自动成像] 已设置数据文件路径: {saved_file_path}")

            # 3. 使用 QTimer 延迟触发2D成像，确保界面切换完成
            QtCore.QTimer.singleShot(200, self.auto_generate_2d_image)

        except Exception as e:
            self._log(f"[错误] 自动切换和成像失败: {e}")
            QMessageBox.warning(self.parent, "警告", f"自动切换和成像过程出错：\n{e}")

    def _set_busy(self, busy: bool):
        """
        设置忙碌状态

        Args:
            busy: 是否忙碌
        """
        if hasattr(self.parent, "set_busy"):
            self.parent.set_busy(busy)

    def _log(self, text: str):
        """
        记录日志到父窗口的日志区域

        Args:
            text: 日志文本
        """
        if hasattr(self.parent, "log"):
            self.parent.log(text)
        logger.info(text)
