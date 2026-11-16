"""
单元测试 - cylinder_3d模块
测试3D重建管道的核心函数
"""
import pytest
import numpy as np


@pytest.mark.unit
def test_median_filter_1d_basic():
    """测试中值滤波基本功能"""
    from algorithms.cylinder_3d import median_filter_1d

    # 准备测试数据
    data = np.array([1, 5, 2, 8, 3])

    # 执行函数（参数名是ksize，不是window）
    result = median_filter_1d(data, ksize=3)

    # 验证结果
    assert result.shape == data.shape, "输出形状应该与输入相同"
    assert isinstance(result, np.ndarray), "应该返回numpy数组"
    assert result.dtype == np.float64, "应该返回float64类型"


@pytest.mark.unit
def test_median_filter_1d_edge_cases():
    """测试中值滤波边界情况"""
    from algorithms.cylinder_3d import median_filter_1d

    # 空数组
    empty = np.array([])
    result = median_filter_1d(empty, ksize=3)
    assert len(result) == 0, "空数组应该返回空数组"

    # 单个元素
    single = np.array([5])
    result = median_filter_1d(single, ksize=3)
    assert result[0] == 5, "单个元素应该保持不变"

    # 两个元素
    two = np.array([3, 7])
    result = median_filter_1d(two, ksize=3)
    assert len(result) == 2, "长度应该保持不变"


@pytest.mark.unit
def test_median_filter_1d_window_size():
    """测试不同窗口大小"""
    from algorithms.cylinder_3d import median_filter_1d

    data = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9])

    # 窗口大小为1（应该返回原数组）
    result1 = median_filter_1d(data, ksize=1)
    np.testing.assert_array_equal(result1, data)

    # 窗口大小为5
    result5 = median_filter_1d(data, ksize=5)
    assert result5.shape == data.shape


@pytest.mark.unit
def test_median_filter_1d_noise_removal():
    """测试中值滤波去噪效果"""
    from algorithms.cylinder_3d import median_filter_1d

    # 创建带噪声的数据（中间有一个异常值）
    data = np.array([1.0, 1.0, 1.0, 100.0, 1.0, 1.0, 1.0])

    result = median_filter_1d(data, ksize=3)

    # 异常值应该被平滑
    assert result[3] < 50, "异常值应该被中值滤波平滑"


@pytest.mark.unit
def test_read_data_from_file(sample_data_file):
    """测试从文件读取十六进制数据"""
    from algorithms.cylinder_3d import read_data_from_file

    # 读取测试数据文件
    data = read_data_from_file(str(sample_data_file))

    # 验证结果
    assert isinstance(data, np.ndarray), "应该返回numpy数组"
    assert len(data) > 0, "应该读取到数据"
    assert data.dtype in [np.uint8, np.int32, np.float64], "数据类型应该是数值类型"


@pytest.mark.unit
def test_read_data_from_file_nonexistent():
    """测试读取不存在的文件"""
    from algorithms.cylinder_3d import read_data_from_file

    # 尝试读取不存在的文件应该抛出异常
    with pytest.raises(FileNotFoundError):
        read_data_from_file("nonexistent_file.txt")
