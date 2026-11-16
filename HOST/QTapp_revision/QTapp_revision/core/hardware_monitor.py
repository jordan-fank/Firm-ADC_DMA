# -*- coding: utf-8 -*-
import time
import subprocess
import psutil
import logging
from PyQt5 import QtCore

logger = logging.getLogger(__name__)


def _query_nvidia_smi() -> dict:
    """通过 nvidia-smi 获取 GPU 使用率和温度，失败返回 None。"""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            timeout=2, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode().strip()
        parts = out.split(",")
        return {"gpu_percent": float(parts[0].strip()), "gpu_temp": float(parts[1].strip())}
    except Exception:
        return {"gpu_percent": None, "gpu_temp": None}


class HardwareMonitorWorker(QtCore.QThread):
    """
    硬件监控工作线程

    在后台线程中定期采集硬件数据，通过信号发送到主线程更新UI
    """
    # 信号：传递硬件状态字典
    stats_updated = QtCore.pyqtSignal(dict)

    def __init__(self, interval_ms=1000, parent=None):
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._running = False
        self._hw_last_net = None
        self._hw_last_time = None

    def run(self):
        """线程执行体：定期采集硬件数据"""
        self._running = True
        self._hw_last_net = psutil.net_io_counters()
        self._hw_last_time = time.time()

        while self._running:
            try:
                stats = self._collect_hardware_stats()
                self.stats_updated.emit(stats)
            except Exception as e:
                logger.error(f"[硬件监控] 采集数据失败: {e}")

            # 等待指定时间间隔
            self.msleep(self._interval_ms)

    def stop(self):
        """停止监控线程"""
        self._running = False
        logger.info("[硬件监控] 停止监控")

    def set_interval(self, interval_ms: int):
        """动态调整监控间隔"""
        self._interval_ms = max(500, min(5000, interval_ms))

    def _collect_hardware_stats(self) -> dict:
        """
        采集硬件状态数据

        Returns:
            包含所有硬件指标的字典
        """
        # CPU使用率
        cpu_percent = psutil.cpu_percent(interval=None)

        # 内存使用率
        vm = psutil.virtual_memory()
        ram_percent = vm.percent

        # 磁盘使用率
        du = psutil.disk_usage("/")
        disk_percent = du.percent

        # 网络速度计算
        now_net = psutil.net_io_counters()
        now_t = time.time()
        dt = max(now_t - self._hw_last_time, 1e-6)
        up_bps = (now_net.bytes_sent - self._hw_last_net.bytes_sent) / dt
        down_bps = (now_net.bytes_recv - self._hw_last_net.bytes_recv) / dt
        self._hw_last_net = now_net
        self._hw_last_time = now_t

        # CPU温度（可选）
        cpu_temp = None
        try:
            temps = psutil.sensors_temperatures(fallback=False)
            if temps:
                for _, entries in temps.items():
                    vals = [e.current for e in entries if e.current is not None]
                    if vals:
                        cpu_temp = sum(vals) / len(vals)
                        break
        except (AttributeError, OSError) as e:
            # AttributeError: 平台不支持温度传感器
            # OSError: 传感器访问失败
            pass

        # GPU使用率和温度（通过 nvidia-smi）
        gpu_stats = _query_nvidia_smi()
        gpu_percent = gpu_stats["gpu_percent"]
        gpu_temp = gpu_stats["gpu_temp"]

        return dict(
            cpu_percent=cpu_percent,
            gpu_percent=gpu_percent,
            ram_percent=ram_percent,
            disk_percent=disk_percent,
            cpu_temp=cpu_temp,
            gpu_temp=gpu_temp,
            net_up_bps=up_bps,
            net_down_bps=down_bps,
        )
