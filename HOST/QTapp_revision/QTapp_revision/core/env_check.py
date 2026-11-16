# -*- coding: utf-8 -*-
"""
环境检查模块
检查 PyTorch 和 CUDA 是否可用
"""


def check_pytorch_available():
    """
    检查 PyTorch 是否可用

    返回:
        (bool, str): (是否可用, 错误信息)
    """
    try:
        import torch
        return True, ""
    except ImportError:
        return False, "PyTorch 未安装"
    except Exception as e:
        return False, f"PyTorch 加载失败: {str(e)}"


def check_cuda_available():
    """
    检查 CUDA 是否可用

    返回:
        bool: CUDA 是否可用
    """
    try:
        import torch
        return torch.cuda.is_available()
    except (ImportError, RuntimeError) as e:
        # ImportError: torch未安装
        # RuntimeError: CUDA初始化失败
        return False


def get_device_info():
    """
    获取设备信息

    返回:
        str: 设备信息描述
    """
    try:
        import torch
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            return f"GPU: {device_name}"
        else:
            return "CPU"
    except (ImportError, RuntimeError) as e:
        # ImportError: torch未安装
        # RuntimeError: CUDA相关错误
        return "未知"
