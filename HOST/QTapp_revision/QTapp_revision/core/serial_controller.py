# -*- coding: utf-8 -*-
"""
串口控制器模块

职责：
1. 串口初始化和配置
2. 串口打开/关闭
3. 串口数据读写
4. 串口状态管理
5. 串口数据保存

从 MainWindow 中提取，降低主窗口复杂度
"""

import os
import time
import logging
from typing import Optional

from PyQt5 import QtCore
from PyQt5.QtWidgets import QMessageBox, QFileDialog
from PyQt5.QtSerialPort import QSerialPort, QSerialPortInfo
from utils.speed_frame_filter import SpeedFrameStreamParser

logger = logging.getLogger(__name__)


class _WriteRelay(QtCore.QObject):
    """将跨线程的 write_data 调用安全转发到主线程。"""
    write_requested = QtCore.pyqtSignal(bytes)


class _SerialSignals(QtCore.QObject):
    speed_updated = QtCore.pyqtSignal(dict)


class SerialController:
    """
    串口控制器：负责串口通信的所有操作

    该类封装了串口的初始化、打开、关闭、数据读写、状态管理等功能。
    从 MainWindow 中提取，实现关注点分离。
    """

    def __init__(self, parent, ui, config, get_app_dir_func):
        """
        初始化串口控制器

        Args:
            parent: 父窗口对象（用于创建QSerialPort和显示消息框）
            ui: UI对象，包含所有UI控件的引用
            config: 配置管理器对象
            get_app_dir_func: 获取应用目录的函数
        """
        self.parent = parent
        self.ui = ui
        self.config = config
        self.get_app_dir = get_app_dir_func

        # 跨线程写串口的信号转发器（子线程调用 write_data 时安全转发到主线程）
        self._write_relay = _WriteRelay(parent)
        self._write_relay.write_requested.connect(self._do_write, QtCore.Qt.QueuedConnection)
        self._signals = _SerialSignals(parent)
        self.speed_updated = self._signals.speed_updated
        self._speed_parser = SpeedFrameStreamParser()
        self._speed_frame_count = 0
        self._speed_rpm_sum = 0
        self._speed_latest_rpm = None
        self._speed_min_rpm = None
        self._speed_max_rpm = None

        # 串口对象
        self.serial: Optional[QSerialPort] = None

        # 串口状态标志
        self._serial_is_open = False

        # 全量内存缓冲区，采集期间不写文件，保存时一次性写出
        self._serial_data_buffer = []

        # UI更新批量缓冲区和定时器
        self._ui_update_buffer = []
        self._ui_update_timer = QtCore.QTimer(parent)
        self._ui_update_timer.timeout.connect(self._flush_ui_buffer)
        self._ui_update_timer.setInterval(200)  # 每200ms更新一次UI，平衡实时性和性能

        # 串口列表刷新定时器（热拔插支持）
        self._port_refresh_timer = QtCore.QTimer(parent)
        self._port_refresh_timer.timeout.connect(self._refresh_serial_ports)
        self._port_refresh_timer.setInterval(2000)  # 每2秒扫描一次

        logger.info("[串口控制器] 初始化完成")

    def initialize(self):
        """
        初始化串口：
        1. 创建 QSerialPort 对象
        2. 连接信号
        3. 扫描可用串口
        4. 填充UI控件
        5. 设置初始状态
        """
        # 创建串口对象
        self.serial = QSerialPort(self.parent)
        self.serial.readyRead.connect(self._on_serial_ready_read)

        logger.info("[串口] 初始化串口缓冲区，最大容量: 100000条")

        # 填充串口列表并启动定时刷新
        self._refresh_serial_ports()
        self._port_refresh_timer.start()

        # 设置初始状态
        self._update_status(False)

        # SaveLineEdit 默认填入 output 目录
        if hasattr(self.ui, "SaveLineEdit"):
            default_save_dir = os.path.join(self.get_app_dir(), "output")
            os.makedirs(default_save_dir, exist_ok=True)
            self.ui.SaveLineEdit.setText(default_save_dir)

        logger.info("[串口] 初始化完成")

    def _refresh_serial_ports(self):
        if not hasattr(self.ui, "SerialcomboBox"):
            return
        combo = self.ui.SerialcomboBox
        current = combo.currentText()
        ports = [p.portName() for p in QSerialPortInfo.availablePorts()]
        if ports == [combo.itemText(i) for i in range(combo.count())]:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(ports)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        logger.info(f"[串口] 扫描到 {len(ports)} 个可用串口")

    def is_open(self) -> bool:
        """
        检查串口是否打开

        Returns:
            bool: 串口是否打开
        """
        return self.serial is not None and self.serial.isOpen()

    def ensure_open(self) -> bool:
        """
        确保串口已打开，如果未打开则显示警告

        Returns:
            bool: 串口是否打开
        """
        if not self.is_open():
            QMessageBox.warning(self.parent, "警告", "请先打开串口")
            return False
        return True

    def open_serial(self) -> bool:
        """
        打开串口

        从UI控件读取串口参数（端口号、波特率、数据位、停止位、校验位），
        配置串口并尝试打开。

        Returns:
            bool: 是否成功打开串口
        """
        # 检查UI控件是否存在
        if not hasattr(self.ui, "SerialcomboBox"):
            QMessageBox.warning(self.parent, "警告", "UI 中未找到串口相关控件")
            return False

        # 读取波特率
        baud_text = self.ui.BaudcomboBox.currentText().strip()
        try:
            baud_rate = int(baud_text)
        except ValueError:
            QMessageBox.warning(self.parent, "警告", f"无效波特率：{baud_text}")
            return False

        # 读取数据位
        db_text = self.ui.DatacomboBox.currentText().strip()
        if db_text == "5":
            data_bits = QSerialPort.Data5
        elif db_text == "6":
            data_bits = QSerialPort.Data6
        elif db_text == "7":
            data_bits = QSerialPort.Data7
        else:
            data_bits = QSerialPort.Data8

        # 读取停止位
        sb_text = self.ui.StopcomboBox.currentText().strip()
        if sb_text == "1":
            stop_bits = QSerialPort.OneStop
        elif sb_text == "1.5":
            stop_bits = QSerialPort.OneAndHalfStop
        else:
            stop_bits = QSerialPort.TwoStop

        # 校验位（目前 UI 只有 none）
        parity = QSerialPort.NoParity

        # 读取端口名
        port_name = self.ui.SerialcomboBox.currentText().strip()
        if not port_name:
            QMessageBox.warning(self.parent, "警告", "请选择串口号")
            return False

        # 如果串口已打开，先关闭
        if self.serial.isOpen():
            self.serial.close()

        # 配置串口参数
        self.serial.setPortName(port_name)
        self.serial.setBaudRate(baud_rate)
        self.serial.setDataBits(data_bits)
        self.serial.setStopBits(stop_bits)
        self.serial.setParity(parity)

        # 尝试打开串口
        if self.serial.open(QtCore.QIODevice.ReadWrite):
            self._reset_speed_stats()

            # 设置接收缓冲区大小为20MB
            self.serial.setReadBufferSize(20 * 1024 * 1024)

            # 启动UI更新定时器
            self._ui_update_timer.start()

            QMessageBox.information(self.parent, "提示", "串口打开成功")
            self._log(f"[SERIAL] 打开串口 {port_name} @ {baud_rate}")
            self._update_status(True)
            return True
        else:
            QMessageBox.critical(self.parent, "提示", f"串口打开失败：{self.serial.errorString()}")
            self._update_status(False)
            return False

    def close_serial(self):
        """关闭串口，同时关闭实时写文件"""
        if self.serial.isOpen():
            # 停止UI更新定时器
            self._ui_update_timer.stop()
            # 刷新剩余的UI缓冲区
            self._flush_ui_buffer()

            self.serial.close()
            if self.serial.isOpen():
                QMessageBox.critical(self.parent, "提示", "串口关闭失败")
            else:
                QMessageBox.information(self.parent, "提示", "串口已关闭")
                self._log("[SERIAL] 串口关闭")
        else:
            QMessageBox.information(self.parent, "提示", "串口已处于关闭状态")

        self._update_status(False)

    def clear_view(self):
        """清空UI显示和内存缓冲区"""
        self._serial_data_buffer.clear()
        self._ui_update_buffer.clear()
        self._reset_speed_stats()
        if hasattr(self.ui, "plainTextEdit"):
            self.ui.plainTextEdit.clear()
        logger.info("[串口] 已清空数据")

    def write_data(self, data: bytes) -> bool:
        if not self.is_open():
            logger.warning("[串口] 尝试写入数据，但串口未打开")
            return False
        # 非主线程时通过信号转发，避免跨线程操作 QSerialPort 导致崩溃
        if QtCore.QThread.currentThread() is not self._write_relay.thread():
            self._write_relay.write_requested.emit(data)
            return True
        return self._do_write(data)

    def _do_write(self, data: bytes) -> bool:
        if not self.is_open():
            return False
        try:
            bytes_written = self.serial.write(data)
            if bytes_written == -1:
                logger.error(f"[串口] 写入失败：{self.serial.errorString()}")
                return False
            logger.debug(f"[串口] 写入 {bytes_written} 字节")
            return True
        except Exception as e:
            logger.error(f"[串口] 写入异常：{e}")
            return False

    def prepare_save(self):
        """生成目标路径，返回内存数据和路径供子线程写入。"""
        if not self._serial_data_buffer:
            QMessageBox.information(self.parent, "提示", "当前没有可保存的串口数据")
            return None, None

        base_dir = self.ui.SaveLineEdit.text().strip() if hasattr(self.ui, "SaveLineEdit") else ""
        if not base_dir or not os.path.isdir(base_dir):
            base_dir = os.path.join(self.get_app_dir(), "output")
        os.makedirs(base_dir, exist_ok=True)

        fname = time.strftime("serial_%Y%m%d_%H%M%S.txt")
        target_path = os.path.join(base_dir, fname)
        text = "\n".join(self._serial_data_buffer)
        return text, target_path

    def write_data_file(self, text: str, target_path: str) -> tuple[bool, str]:
        """将内存数据写入文件。"""
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(text)
            return True, ""
        except Exception as e:
            return False, str(e)

    def on_save_file_done(self, target_path: str):
        """文件写入成功后，在主线程调用以更新 UI。"""
        if hasattr(self.ui, "SaveLineEdit"):
            self.ui.SaveLineEdit.setText(os.path.dirname(target_path))
        QMessageBox.information(self.parent, "成功", f"串口数据已保存到：\n{target_path}")
        self._log(f"[SERIAL] 数据保存到 {target_path}")

    def get_buffer_size(self) -> int:
        """
        获取当前缓冲区中的数据条数

        Returns:
            int: 缓冲区数据条数
        """
        return len(self._serial_data_buffer)

    def _on_serial_ready_read(self):
        if not self.serial.isOpen():
            return
        data_bytes = bytes(self.serial.readAll())
        if not data_bytes:
            return

        self._handle_speed_frames(data_bytes)

        hex_str = " ".join(f"{b:02X}" for b in data_bytes)

        self._serial_data_buffer.append(hex_str)

        # 批量更新UI：先缓存数据，由定时器统一刷新
        self._ui_update_buffer.append(hex_str)

    def _reset_speed_stats(self):
        self._speed_parser.reset()
        self._speed_frame_count = 0
        self._speed_rpm_sum = 0
        self._speed_latest_rpm = None
        self._speed_min_rpm = None
        self._speed_max_rpm = None
        self._signals.speed_updated.emit(self.get_speed_summary())

    def _handle_speed_frames(self, data_bytes: bytes):
        frames = self._speed_parser.feed(data_bytes)
        if not frames:
            return

        for frame in frames:
            rpm = int(frame["rpm"])
            self._speed_frame_count += 1
            self._speed_rpm_sum += rpm
            self._speed_latest_rpm = rpm
            self._speed_min_rpm = rpm if self._speed_min_rpm is None else min(self._speed_min_rpm, rpm)
            self._speed_max_rpm = rpm if self._speed_max_rpm is None else max(self._speed_max_rpm, rpm)

        self._signals.speed_updated.emit(self.get_speed_summary())

    def get_speed_summary(self) -> dict:
        avg = None
        if self._speed_frame_count:
            avg = self._speed_rpm_sum / self._speed_frame_count
        return {
            "source": "serial",
            "frame_count": self._speed_frame_count,
            "latest_rpm": self._speed_latest_rpm,
            "avg_rpm": avg,
            "min_rpm": self._speed_min_rpm,
            "max_rpm": self._speed_max_rpm,
        }

    def _flush_ui_buffer(self):
        """定时批量更新UI，避免频繁刷新导致卡顿"""
        if not self._ui_update_buffer:
            return

        if hasattr(self.ui, "plainTextEdit"):
            # 批量追加文本
            text = "\n".join(self._ui_update_buffer)
            self.ui.plainTextEdit.appendPlainText(text)

            # 限制UI显示行数，避免界面卡顿
            doc = self.ui.plainTextEdit.document()
            if doc.blockCount() > 2000:
                cursor = self.ui.plainTextEdit.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.movePosition(cursor.Down, cursor.KeepAnchor, doc.blockCount() - 2000)
                cursor.removeSelectedText()

        self._ui_update_buffer.clear()

    def _update_status(self, is_open: bool):
        """
        更新串口状态文本 + 控件可用状态

        Args:
            is_open: 串口是否打开
        """
        self._serial_is_open = bool(is_open)

        # 更新状态文本
        if hasattr(self.ui, "labelSerialStatusValue"):
            self.ui.labelSerialStatusValue.setText("已连接" if is_open else "未连接")

        # 更新打开/关闭按钮状态
        if hasattr(self.ui, "OpenButton"):
            self.ui.OpenButton.setEnabled(not is_open)
        if hasattr(self.ui, "CloseButton"):
            self.ui.CloseButton.setEnabled(is_open)

        # 串口参数在打开后禁用，关闭后允许修改
        for name in ["SerialcomboBox", "BaudcomboBox",
                     "DatacomboBox", "StopcomboBox", "ParitycomboBox"]:
            if hasattr(self.ui, name):
                getattr(self.ui, name).setEnabled(not is_open)

    def _log(self, text: str):
        """
        记录日志到父窗口的日志区域

        Args:
            text: 日志文本
        """
        if hasattr(self.parent, "log"):
            self.parent.log(text)
        logger.info(text)
