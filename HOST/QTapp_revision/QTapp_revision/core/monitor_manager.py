# -*- coding: utf-8 -*-
"""
监控管理器模块

职责：
1. 硬件监控（CPU、GPU、内存、磁盘）
2. 服务器状态管理
3. UI状态更新

从 MainWindow 中提取，降低主窗口复杂度
"""

import logging
from typing import Optional

from core.hardware_monitor import HardwareMonitorWorker

logger = logging.getLogger(__name__)


class MonitorManager:
    """
    监控管理器：负责硬件监控和服务器状态管理

    该类封装了硬件监控线程的管理和UI状态更新功能。
    从 MainWindow 中提取，实现关注点分离。
    """

    def __init__(self, parent, ui, config):
        """
        初始化监控管理器

        Args:
            parent: 父窗口对象
            ui: UI对象，包含所有UI控件的引用
            config: 配置管理器对象
        """
        self.parent = parent
        self.ui = ui
        self.config = config

        # 硬件监控工作线程
        self._hw_monitor_worker: Optional[HardwareMonitorWorker] = None

        logger.info("[监控管理器] 初始化完成")

    def setup_hardware_monitoring(self):
        """
        初始化硬件监控（使用独立线程）

        创建硬件监控工作线程，连接信号，并启动监控。
        监控线程会定期收集CPU、GPU、内存、磁盘等硬件信息。
        """
        # 创建硬件监控工作线程
        self._hw_monitor_worker = HardwareMonitorWorker(interval_ms=1000, parent=self.parent)

        # 连接信号：接收硬件状态并更新UI
        self._hw_monitor_worker.stats_updated.connect(self.update_hardware_ui)

        # 启动监控线程
        self._hw_monitor_worker.start()
        logger.info("[硬件监控] 启动独立监控线程")

    def update_hardware_ui(self, stats: dict):
        if self.ui is None:
            return
        # 更新CPU
        if hasattr(self.ui, "progressCPU"):
            self.ui.progressCPU.setValue(int(stats["cpu_percent"]))
        if hasattr(self.ui, "labelCPU"):
            self.ui.labelCPU.setText(f"CPU 使用率：{stats['cpu_percent']:.1f}%")

        # 更新GPU
        gpu_val = stats["gpu_percent"] or 0.0
        if hasattr(self.ui, "progressGPU"):
            self.ui.progressGPU.setValue(int(gpu_val))
        if hasattr(self.ui, "labelGPU"):
            self.ui.labelGPU.setText(
                "GPU 使用率：N/A" if stats["gpu_percent"] is None else f"GPU 使用率：{stats['gpu_percent']:.1f}%"
            )

        # 更新内存
        if hasattr(self.ui, "progressRAM"):
            self.ui.progressRAM.setValue(int(stats["ram_percent"]))
        if hasattr(self.ui, "labelRAM"):
            self.ui.labelRAM.setText(f"内存使用率：{stats['ram_percent']:.1f}%")

        # 更新磁盘
        if hasattr(self.ui, "progressDISK"):
            self.ui.progressDISK.setValue(int(stats["disk_percent"]))
        if hasattr(self.ui, "labelDISK"):
            self.ui.labelDISK.setText(f"磁盘使用率：{stats['disk_percent']:.1f}%")

        # 更新网速
        def _fmt(bps: float) -> str:
            if bps >= 1_000_000:
                return f"{bps/1_000_000:.1f} MB/s"
            elif bps >= 1_000:
                return f"{bps/1_000:.1f} KB/s"
            return f"{bps:.0f} B/s"

        if hasattr(self.ui, "labelNetUpValue"):
            self.ui.labelNetUpValue.setText(_fmt(stats["net_up_bps"]))
        if hasattr(self.ui, "labelNetDownValue"):
            self.ui.labelNetDownValue.setText(_fmt(stats["net_down_bps"]))

    def update_server_status(self, is_online: bool, server_url: str = "", port: int = 8000):
        """
        更新服务器状态显示

        Args:
            is_online: 服务器是否在线
            server_url: 服务器URL（包含IP和端口）
            port: 服务器端口号
        """
        if hasattr(self.ui, "labelStatusDot"):
            # 更新状态圆点颜色
            if is_online:
                # 绿色圆点
                self.ui.labelStatusDot.setStyleSheet("background-color:#22C55E; border-radius:6px;")
            else:
                # 红色圆点
                self.ui.labelStatusDot.setStyleSheet("background-color:#EF4444; border-radius:6px;")

        if hasattr(self.ui, "labelStatusValue"):
            # 更新状态文本
            if is_online:
                self.ui.labelStatusValue.setText("在线 · 设备就绪")
                self.ui.labelStatusValue.setStyleSheet("color:#16A34A;")
                # 设置tooltip显示服务器信息
                self.ui.labelStatusValue.setToolTip(f"HTTP服务器地址：{server_url}\n端口：{port}")
            else:
                self.ui.labelStatusValue.setText("离线 · 服务未启动")
                self.ui.labelStatusValue.setStyleSheet("color:#EF4444;")
                self.ui.labelStatusValue.setToolTip("HTTP服务器启动失败")

    def set_monitoring_interval(self, interval_ms: int):
        """
        设置监控间隔

        Args:
            interval_ms: 监控间隔（毫秒）
        """
        if self._hw_monitor_worker:
            self._hw_monitor_worker.set_interval(interval_ms)

    def stop(self):
        """
        停止监控线程

        在应用关闭时调用，确保线程正确停止
        """
        if self._hw_monitor_worker:
            self._hw_monitor_worker.stop()
            if not self._hw_monitor_worker.wait(2000):
                logger.warning("[硬件监控] 线程未在2秒内停止，强制终止")
                self._hw_monitor_worker.terminate()
                self._hw_monitor_worker.wait(500)
            logger.info("[硬件监控] 线程已停止")
