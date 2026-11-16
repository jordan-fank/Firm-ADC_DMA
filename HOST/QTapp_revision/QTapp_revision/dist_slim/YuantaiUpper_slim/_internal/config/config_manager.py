# -*- coding: utf-8 -*-
"""
配置文件管理模块

使用JSON格式保存和加载应用配置
"""
import json
import os
import sys
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _get_app_dir() -> str:
    """获取程序所在目录，兼容开发环境和打包环境。"""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigManager:
    """
    配置管理器

    负责加载、保存和管理应用配置
    """

    def __init__(self, config_file=None):
        if config_file is None:
            config_file = os.path.join(_get_app_dir(), "config", "config.json")
        self.config_file = config_file
        self.config = self._load_default_config()
        self.load()

    def _load_default_config(self) -> Dict[str, Any]:
        """
        加载默认配置

        Returns:
            默认配置字典
        """
        return {
            # 输出路径配置
            "output_dir": "output",
            "auto_naming": True,

            # CloudCompare路径
            "cloudcompare_path": r"C:\Program Files\CloudCompare\CloudCompare.exe",

            # 串口配置
            "serial": {
                "baud_rate": 115200,
                "data_bits": 8,
                "stop_bits": 1,
                "parity": "none",
                "buffer_size": 20971520  # 20MB
            },

            # 硬件监控配置
            "hardware_monitor": {
                "enabled": True,
                "interval_normal": 1000,  # ms
                "interval_busy": 3000     # ms
            },

            # 窗口配置
            "window": {
                "width": 1360,
                "height": 800,
                "remember_size": False,
                "remember_position": False
            },

            # 3D渲染配置
            "rendering_3d": {
                "auto_rotate": True,
                "rotation_speed": 20.0,
                "point_size": 2.0
            },

            # 成像配置
            "imaging": {
                "auto_image_after_save": True,
                "auto_3d": True,
                "flip_horizontal": False,
                "width": 4000
            },

            # HTTP服务器配置
            "http_server": {
                "port": 8000,
                "enabled": True
            }
        }

    def load(self) -> bool:
        """
        从文件加载配置

        Returns:
            是否加载成功
        """
        if not os.path.exists(self.config_file):
            logger.info(f"[配置] 配置文件不存在，使用默认配置: {self.config_file}")
            self.save()  # 保存默认配置
            return False

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                # 合并加载的配置和默认配置（以加载的为准）
                self._merge_config(loaded_config)
            logger.info(f"[配置] 成功加载配置文件: {self.config_file}")
            return True
        except Exception as e:
            logger.error(f"[配置] 加载配置文件失败: {e}")
            return False

    def _merge_config(self, loaded_config: Dict[str, Any]):
        """
        合并配置（深度合并）

        Args:
            loaded_config: 加载的配置
        """
        def deep_merge(base, update):
            for key, value in update.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    deep_merge(base[key], value)
                elif key in base and isinstance(base[key], list) and isinstance(value, list):
                    base[key] = value  # 列表直接替换（以加载的配置为准）
                else:
                    base[key] = value

        deep_merge(self.config, loaded_config)

    def save(self) -> bool:
        """
        保存配置到文件

        Returns:
            是否保存成功
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            logger.info(f"[配置] 配置已保存: {self.config_file}")
            return True
        except Exception as e:
            logger.error(f"[配置] 保存配置失败: {e}")
            return False

    def get(self, key_path: str, default=None) -> Any:
        """
        获取配置值（支持点号分隔的路径）

        Args:
            key_path: 配置路径，如 "serial.baud_rate"
            default: 默认值

        Returns:
            配置值
        """
        keys = key_path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, key_path: str, value: Any) -> bool:
        """
        设置配置值（支持点号分隔的路径）

        Args:
            key_path: 配置路径，如 "serial.baud_rate"
            value: 要设置的值

        Returns:
            是否设置成功
        """
        keys = key_path.split('.')
        config = self.config
        for key in keys[:-1]:
            if key not in config or not isinstance(config[key], dict):
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value
        return True

    def reset_to_default(self):
        """重置为默认配置"""
        self.config = self._load_default_config()
        self.save()
        logger.info("[配置] 已重置为默认配置")


# 全局配置实例（单例模式）
_config_manager = None


def get_config_manager() -> ConfigManager:
    """
    获取全局配置管理器实例

    Returns:
        ConfigManager实例
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
