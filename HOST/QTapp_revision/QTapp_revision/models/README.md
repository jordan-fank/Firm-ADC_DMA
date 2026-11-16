# 模型文件目录

此目录用于存放分割算法所需的模型文件。

## 所需文件

### 1. 深度学习模型（可选）
- **文件名**: `autoencoder_model.pth`
- **说明**: PyTorch 自编码器模型文件
- **用途**: 用于深度学习方法的缺陷检测

### 2. 字典学习字典
- **文件名**: `dictionary.pkl`
- **说明**: Pickle 格式的字典文件
- **用途**: 用于字典学习方法的缺陷检测

### 3. 参考图像
- **文件名**: `reference.png`
- **说明**: 参考图像文件（无缺陷的标准图像）
- **用途**: 用于字典学习方法作为对比基准

## 配置说明

这些文件的路径可以在 `config/config.json` 中的 `segmentation` 部分进行配置：

```json
"segmentation": {
    "model_path": "models/autoencoder_model.pth",
    "dictionary_path": "models/dictionary.pkl",
    "reference_image": "models/reference.png"
}
```

## 打包说明

使用 PyInstaller 打包时，需要在 `.spec` 文件中添加 models 目录：

```python
datas=[
    ('models', 'models'),
    # ... 其他数据文件
]
```

这样可以确保模型文件被包含在打包后的程序中。
