"""
pytest配置文件
定义测试fixtures和全局配置
"""
import pytest
import sys
from pathlib import Path
import tempfile

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def temp_dir():
    """创建临时目录用于测试"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_data_file(temp_dir):
    """创建测试用的数据文件（十六进制格式）"""
    data_file = temp_dir / "test_data.txt"
    # 模拟真实的十六进制数据格式
    test_data = """01 02 03 04 05 06 07 08
09 0A 0B 0C 0D 0E 0F 10
11 12 13 14 15 16 17 18
19 1A 1B 1C 1D 1E 1F 20
"""
    data_file.write_text(test_data, encoding='utf-8')
    return data_file


@pytest.fixture
def sample_image(temp_dir):
    """创建测试用的图像文件"""
    import numpy as np
    try:
        import cv2
        # 创建简单的测试图像
        img = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        img_path = temp_dir / "test_image.png"
        cv2.imwrite(str(img_path), img)
        return img_path
    except ImportError:
        pytest.skip("OpenCV not available")


@pytest.fixture
def sample_config(temp_dir):
    """创建测试用的配置文件"""
    import json
    config_file = temp_dir / "config.json"
    config_data = {
        "output_dir": str(temp_dir / "output"),
        "serial": {
            "port": "COM1",
            "baud_rate": 115200
        },
        "window": {
            "width": 1360,
            "height": 800
        }
    }
    config_file.write_text(json.dumps(config_data, indent=2), encoding='utf-8')
    return config_file
