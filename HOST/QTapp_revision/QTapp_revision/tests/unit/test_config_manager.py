"""
单元测试 - config_manager模块
测试配置管理器的功能
"""
import pytest
import json


@pytest.mark.unit
def test_config_manager_initialization(temp_dir):
    """测试配置管理器初始化"""
    from config.config_manager import ConfigManager

    config_file = temp_dir / "config.json"
    config = ConfigManager(str(config_file))

    assert config is not None
    assert config.config_file == str(config_file)


@pytest.mark.unit
def test_config_get_set(temp_dir):
    """测试配置的获取和设置"""
    from config.config_manager import ConfigManager

    config_file = temp_dir / "config.json"
    config = ConfigManager(str(config_file))

    # 设置值
    config.set("test.key", "test_value")
    assert config.get("test.key") == "test_value"

    # 设置嵌套值
    config.set("nested.deep.key", 123)
    assert config.get("nested.deep.key") == 123


@pytest.mark.unit
def test_config_get_default(temp_dir):
    """测试获取不存在的键时返回默认值"""
    from config.config_manager import ConfigManager

    config_file = temp_dir / "config.json"
    config = ConfigManager(str(config_file))

    # 不存在的键应该返回默认值
    assert config.get("nonexistent.key", "default") == "default"
    assert config.get("another.missing.key", 42) == 42


@pytest.mark.unit
def test_config_persistence(temp_dir):
    """测试配置持久化"""
    from config.config_manager import ConfigManager

    config_file = temp_dir / "config.json"

    # 创建配置并设置值
    config1 = ConfigManager(str(config_file))
    config1.set("persistent.key", "persistent_value")
    config1.save()

    # 创建新实例，应该能读取到之前保存的值
    config2 = ConfigManager(str(config_file))
    assert config2.get("persistent.key") == "persistent_value"


@pytest.mark.unit
def test_config_dot_notation(temp_dir):
    """测试点号表示法访问嵌套配置"""
    from config.config_manager import ConfigManager

    config_file = temp_dir / "config.json"
    config = ConfigManager(str(config_file))

    # 使用点号设置嵌套值
    config.set("level1.level2.level3", "deep_value")

    # 使用点号获取
    assert config.get("level1.level2.level3") == "deep_value"

    # 获取中间层级
    level2 = config.get("level1.level2")
    assert isinstance(level2, dict)
    assert level2["level3"] == "deep_value"


@pytest.mark.unit
def test_config_get_all(temp_dir):
    """测试获取所有配置"""
    from config.config_manager import ConfigManager

    config_file = temp_dir / "config.json"
    config = ConfigManager(str(config_file))

    config.set("key1", "value1")
    config.set("key2", "value2")

    # ConfigManager没有get_all()方法，直接访问config属性
    all_config = config.config
    assert isinstance(all_config, dict)
    assert "key1" in all_config
    assert "key2" in all_config


@pytest.mark.unit
def test_config_singleton():
    """测试单例模式"""
    from config.config_manager import get_config_manager

    config1 = get_config_manager()
    config2 = get_config_manager()

    # 应该返回同一个实例
    assert config1 is config2

    # 在一个实例中设置的值，在另一个实例中应该可见
    config1.set("singleton.test", "value")
    assert config2.get("singleton.test") == "value"
