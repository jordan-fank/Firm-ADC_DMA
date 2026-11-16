"""
冒烟测试 - 快速验证核心功能
这些测试确保应用的关键路径不被破坏
"""
import pytest
import sys
from pathlib import Path


@pytest.mark.smoke
def test_python_version():
    """验证Python版本"""
    assert sys.version_info >= (3, 8), "需要Python 3.8或更高版本"


@pytest.mark.smoke
def test_imports_main():
    """测试主模块能否导入"""
    try:
        import main
        assert hasattr(main, 'main'), "main模块应该有main函数"
    except ImportError as e:
        pytest.fail(f"无法导入main模块: {e}")


@pytest.mark.smoke
def test_imports_config_manager():
    """测试配置管理器能否导入"""
    try:
        from config.config_manager import get_config_manager, ConfigManager
        assert get_config_manager is not None
        assert ConfigManager is not None
    except ImportError as e:
        pytest.fail(f"无法导入config_manager: {e}")


@pytest.mark.smoke
def test_imports_cylinder_3d():
    """测试3D管道模块能否导入"""
    try:
        from algorithms.cylinder_3d import run_pipeline_3d, median_filter_1d
        assert run_pipeline_3d is not None
        assert median_filter_1d is not None
    except ImportError as e:
        pytest.fail(f"无法导入cylinder_3d: {e}")


@pytest.mark.smoke
def test_imports_defect_detection():
    """测试缺陷检测模块能否导入"""
    try:
        from algorithms.defect_detection_unified import Reconstructor
        assert Reconstructor is not None
    except ImportError as e:
        pytest.fail(f"无法导入defect_detection_unified: {e}")


@pytest.mark.smoke
def test_imports_hardware_monitor():
    """测试硬件监控模块能否导入"""
    try:
        from core.hardware_monitor import HardwareMonitorWorker
        assert HardwareMonitorWorker is not None
    except ImportError as e:
        pytest.fail(f"无法导入hardware_monitor: {e}")


@pytest.mark.smoke
def test_config_manager_singleton():
    """测试配置管理器单例模式"""
    from config.config_manager import get_config_manager

    config1 = get_config_manager()
    config2 = get_config_manager()

    assert config1 is config2, "ConfigManager应该是单例"


@pytest.mark.smoke
def test_config_manager_basic_operations(temp_dir):
    """测试配置管理器基本操作"""
    from config.config_manager import ConfigManager

    # 创建临时配置
    config_file = temp_dir / "test_config.json"
    config = ConfigManager(str(config_file))

    # 测试设置和获取
    config.set("test.key", "test_value")
    assert config.get("test.key") == "test_value"

    # 测试默认值
    assert config.get("nonexistent.key", "default") == "default"


@pytest.mark.smoke
def test_env_check_functions():
    """测试环境检查函数"""
    try:
        from core.env_check import check_pytorch_available, check_cuda_available

        # check_pytorch_available 返回 (bool, str) 元组
        pytorch_result = check_pytorch_available()
        assert isinstance(pytorch_result, tuple), "check_pytorch_available应该返回元组"
        assert len(pytorch_result) == 2, "应该返回(bool, str)元组"
        assert isinstance(pytorch_result[0], bool), "第一个元素应该是布尔值"
        assert isinstance(pytorch_result[1], str), "第二个元素应该是字符串"

        # check_cuda_available 返回布尔值
        cuda_available = check_cuda_available()
        assert isinstance(cuda_available, bool), "check_cuda_available应该返回布尔值"

    except ImportError as e:
        pytest.fail(f"无法导入env_check: {e}")


@pytest.mark.smoke
def test_numpy_available():
    """验证numpy可用"""
    try:
        import numpy as np
        # 简单测试numpy功能
        arr = np.array([1, 2, 3])
        assert arr.sum() == 6
    except ImportError:
        pytest.fail("numpy未安装")


@pytest.mark.smoke
def test_pyqt5_available():
    """验证PyQt5可用"""
    try:
        from PyQt5 import QtCore, QtWidgets, QtGui
        from PyQt5.QtSerialPort import QSerialPort
        assert QtCore.QT_VERSION_STR is not None
    except ImportError as e:
        pytest.fail(f"PyQt5未正确安装: {e}")


@pytest.mark.smoke
def test_cv2_available():
    """验证OpenCV可用"""
    try:
        import cv2
        assert cv2.__version__ is not None
    except ImportError:
        pytest.fail("OpenCV未安装")


@pytest.mark.smoke
def test_psutil_available():
    """验证psutil可用"""
    try:
        import psutil
        # 测试基本功能
        cpu_percent = psutil.cpu_percent(interval=0.1)
        assert isinstance(cpu_percent, (int, float))
    except ImportError:
        pytest.fail("psutil未安装")
