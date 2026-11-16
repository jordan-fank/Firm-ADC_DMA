"""
资源路径工具函数
统一管理资源文件路径的获取
"""
import os
import sys


def resource_path(rel_path: str) -> str:
    """
    获取资源文件的绝对路径：
    - 开发环境：以项目根目录为基准
    - PyInstaller 打包后：以 _MEIPASS 临时目录为基准

    Args:
        rel_path: 相对路径

    Returns:
        str: 资源文件的绝对路径
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后的环境
        base_path = sys._MEIPASS
    else:
        # 开发环境：获取项目根目录（utils目录的父目录）
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, rel_path)
