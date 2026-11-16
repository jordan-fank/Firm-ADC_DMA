from __future__ import annotations

import threading
import time
from typing import Optional

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

# Modbus 寄存器地址
REG_DISPLACEMENT = 0x0806   # 目标位移（32位，占2个寄存器）
REG_TRIGGER = 0x3608        # 通信 DI 控制 / 触发寄存器
REG_STATUS = 0x3688         # 通信读取 DO 状态；当前假设仅启用 DO7=COIN，因此完成值为 128

TRIGGER_VALUE = 0x0200      # DI25 有效
TRIGGER_RELEASE = 0x0000    # DI25 无效
STATUS_DONE = 128           # 仅在只启用 DO7 时成立


class MotorController:
    """
    基于 Modbus RTU 的伺服电机控制器。

    当前版本采用如下前提：
    1. P09.11 已设置为足够长，能覆盖单次最长运动时间。
    2. P09.09 只启用了 DO7(COIN)，因此读取 0x3688 时，值为 128 表示运动完成。
    3. 每次运动流程为：写位移 -> 写 0x200 启动 -> 等待状态=128 -> 写 0 释放。
    """

    def __init__(self) -> None:
        self.client: Optional[ModbusSerialClient] = None
        self.connected = False
        self._lock = threading.Lock()
        self._port: Optional[str] = None
        self._stop_flag = threading.Event()

    # -------------------- 连接管理 --------------------
    def connect(self, port: str) -> tuple[bool, str]:
        if self.client:
            self.disconnect()

        self.client = ModbusSerialClient(
            port=port,
            baudrate=9600,
            bytesize=8,
            stopbits=2,
            parity="N",
            timeout=1,
            retries=3,
        )

        if self.client.connect():
            self.connected = True
            self._port = port
            return True, ""
        self.connected = False
        self._port = None
        return False, f"无法连接到 {port}"

    def disconnect(self) -> None:
        with self._lock:
            if self.client:
                self.client.close()
                self.client = None
            self.connected = False
            self._port = None

    # -------------------- 底层读写 --------------------
    def write_register(self, slave_id: int, address: int, value: int) -> tuple[bool, str]:
        if not self.connected or not self.client:
            return False, "未连接设备"
        try:
            with self._lock:
                result = self.client.write_register(address, value, device_id=slave_id)
            if result.isError():
                return False, str(result)
            return True, ""
        except ModbusException as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def write_registers(self, slave_id: int, address: int, values: list[int]) -> tuple[bool, str]:
        if not self.connected or not self.client:
            return False, "未连接设备"
        try:
            with self._lock:
                result = self.client.write_registers(address, values, device_id=slave_id)
            if result.isError():
                return False, str(result)
            return True, ""
        except ModbusException as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def read_register(self, slave_id: int, address: int) -> tuple[Optional[int], str]:
        if not self.connected or not self.client:
            return None, "未连接设备"
        try:
            with self._lock:
                result = self.client.read_holding_registers(address, count=1, device_id=slave_id)
            if result.isError():
                return None, str(result)
            return result.registers[0], ""
        except ModbusException as e:
            return None, str(e)
        except Exception as e:  # noqa: BLE001
            return None, str(e)

    # -------------------- 运动控制 --------------------
    @staticmethod
    def _split_signed_32_to_words(value: int) -> tuple[int, int]:
        value_32 = value & 0xFFFFFFFF
        low = value_32 & 0xFFFF
        high = (value_32 >> 16) & 0xFFFF
        return low, high

    def write_displacement(self, slave_id: int, displacement: int) -> tuple[bool, str]:
        """写入目标位移，不触发运动。负数为反方向。"""
        real_displacement = displacement

        low, high = self._split_signed_32_to_words(real_displacement)
        ok, err = self.write_registers(slave_id, REG_DISPLACEMENT, [low, high])
        if not ok:
            return False, f"写位移失败: {err}"
        return True, ""

    def trigger_motion(self, slave_id: int) -> tuple[bool, str]:
        return self.write_register(slave_id, REG_TRIGGER, TRIGGER_VALUE)

    def release_trigger(self, slave_id: int) -> tuple[bool, str]:
        return self.write_register(slave_id, REG_TRIGGER, TRIGGER_RELEASE)

    def read_status(self, slave_id: int = 1) -> tuple[Optional[int], str]:
        return self.read_register(slave_id, REG_STATUS)

    def is_motion_done(self, slave_id: int = 1) -> tuple[bool, str]:
        """
        当前实现按“状态值恰好等于 128 表示完成”判断。
        这要求驱动器侧只把 DO7(COIN) 映射到通信读取状态中。
        """
        val, err = self.read_status(slave_id)
        if val is None:
            return False, err
        return val == STATUS_DONE, ""

    def move_motor(self, slave_id: int, displacement: int) -> tuple[bool, str]:
        """兼容旧接口：写位移后立即触发。需要等待P09.11不然无法触发新的命令（已不考虑使用）"""
        ok, err = self.write_displacement(slave_id, displacement)
        if not ok:
            return False, err
        ok, err = self.trigger_motion(slave_id)
        if not ok:
            return False, f"触发失败: {err}"
        return True, ""

    def move_motor_and_wait(
        self,
        slave_id: int,
        displacement: int,
        timeout: float = 10.0,
        # 将20ms的轮询改成5ms减少多采集的问题
        poll_interval: float = 0.005,   
        release_after_done: bool = True,
        on_done: callable = None,
        on_start: callable = None,
    ) -> tuple[bool, str]:
        """
        推荐流程：
        1. 写位移
        2. 写 0x200 启动
        3. 轮询 0x3688，等于 128 视为完成
        4. 完成后写 0 释放
        """
        ok, err = self.write_displacement(slave_id, displacement)
        if not ok:
            return False, err

        ok, err = self.trigger_motion(slave_id)
        if not ok:
            return False, f"触发失败: {err}"

        if on_start is not None:
            on_start()

        start = time.time()
        last_status: Optional[int] = None
        while time.time() - start < timeout:
            if self._stop_flag.is_set():
                self.release_trigger(slave_id)
                return False, "已取消"

            status, err = self.read_status(slave_id)
            if status is None:
                # 读取失败说明通信异常，不释放触发器（电机状态未知，避免误操作）
                return False, f"读取状态失败: {err}"

            last_status = status
            if status == STATUS_DONE:
                if on_done is not None:
                    on_done()
                if release_after_done:
                    ok, err = self.release_trigger(slave_id)
                    if not ok:
                        return False, f"运动完成，但写0释放失败: {err}"
                return True, ""

            time.sleep(poll_interval)

        if release_after_done:
            self.release_trigger(slave_id)
        return False, f"等待运动完成超时，最后状态值={last_status}"
