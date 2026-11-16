# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

from PyQt5.QtCore import QObject, pyqtSignal, QTimer
import serial.tools.list_ports

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException


class TanzhengController(QObject):
    status_changed = pyqtSignal(str)

    _SLAVE = 1
    _REG_CTRL = 0x2000
    _VAL_ROTATE = 2
    _VAL_STOP = 1

    def __init__(self, parent=None):
        super(TanzhengController, self).__init__(parent)
        self._client = None  # type: Optional[ModbusSerialClient]
        self.connected = False
        self._port = None  # type: Optional[str]
        self._combo = None
        self._lock = threading.Lock()

        self._port_timer = QTimer(self)
        self._port_timer.timeout.connect(self._refresh_ports)
        self._port_timer.start(2000)

    def bind_combo(self, combo):
        self._combo = combo
        self._refresh_ports()

    def _refresh_ports(self):
        combo = self._combo
        if combo is None:
            return
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        ports = list(serial.tools.list_ports.comports())
        if ports:
            for p in ports:
                desc = "{} - {}".format(p.device, p.description) if p.description else p.device
                combo.addItem(desc, p.device)
            combo.setEnabled(True)
            if current:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        else:
            combo.addItem("未找到串口", None)
            combo.setEnabled(False)
        combo.blockSignals(False)

    def connect(self, port):
        with self._lock:
            if self._client:
                self._client.close()
                self._client = None
                self.connected = False
            self._client = ModbusSerialClient(
                port=port,
                baudrate=9600,
                bytesize=8,
                stopbits=1,
                parity="N",
                timeout=1,
                retries=3,
            )
            if self._client.connect():
                self.connected = True
                self._port = port
            else:
                self._client = None
                self.connected = False
                self._port = None
        if self.connected:
            self.status_changed.emit("已连接 {}".format(port))
        else:
            self.status_changed.emit("连接失败")

    def disconnect_device(self):
        with self._lock:
            if self._client:
                self._client.close()
                self._client = None
            self.connected = False
            self._port = None
        self.status_changed.emit("未连接")

    def _write(self, value):
        with self._lock:
            if not self._client or not self.connected:
                self.status_changed.emit("未连接设备")
                return
            try:
                try:
                    result = self._client.write_register(
                        self._REG_CTRL,
                        value,
                        device_id=self._SLAVE,
                    )
                except TypeError as e:
                    if "device_id" not in str(e):
                        raise
                    result = self._client.write_register(
                        self._REG_CTRL,
                        value,
                        slave=self._SLAVE,
                    )
                if result.isError():
                    logger.error(f"[探针] 写寄存器失败: {result}")
                    self.status_changed.emit("写入失败")
            except (ModbusException, Exception) as e:
                logger.error(f"[探针] 写寄存器失败: {e}")
                self.status_changed.emit("写入失败")

    def rotate(self):
        self._write(self._VAL_ROTATE)

    def stop(self):
        self._write(self._VAL_STOP)
