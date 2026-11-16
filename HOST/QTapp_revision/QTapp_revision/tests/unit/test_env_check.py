"""
单元测试 - env_check模块
测试环境检查功能
"""
import pytest


@pytest.mark.unit
def test_check_pytorch_available():
    """测试PyTorch可用性检查"""
    from core.env_check import check_pytorch_available

    result = check_pytorch_available()

    # 应该返回 (bool, str) 元组
    assert isinstance(result, tuple), "应该返回元组"
    assert len(result) == 2, "应该返回(bool, str)元组"
    assert isinstance(result[0], bool), "第一个元素应该是布尔值"
    assert isinstance(result[1], str), "第二个元素应该是字符串"

    # 不应该抛出异常
    # 如果PyTorch未安装，应该返回(False, "错误信息")而不是崩溃


@pytest.mark.unit
def test_check_cuda_available():
    """测试CUDA可用性检查"""
    from core.env_check import check_cuda_available

    result = check_cuda_available()

    # 应该返回布尔值
    assert isinstance(result, bool)

    # 如果PyTorch未安装或CUDA不可用，应该返回False


@pytest.mark.unit
def test_get_device_info():
    """测试获取设备信息"""
    from core.env_check import get_device_info

    device_info = get_device_info()

    # 应该返回字符串
    assert isinstance(device_info, str), "应该返回字符串"

    # 应该包含设备信息
    assert len(device_info) > 0, "设备信息不应为空"


@pytest.mark.unit
def test_env_check_no_exceptions():
    """测试环境检查函数不会抛出异常"""
    from core.env_check import check_pytorch_available, check_cuda_available, get_device_info

    # 这些函数应该优雅地处理错误，不抛出异常
    try:
        check_pytorch_available()
        check_cuda_available()
        get_device_info()
    except Exception as e:
        pytest.fail(f"环境检查函数不应该抛出异常: {e}")
