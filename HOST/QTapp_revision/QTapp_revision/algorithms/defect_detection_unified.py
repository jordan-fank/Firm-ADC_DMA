# -*- coding: utf-8 -*-
"""
统一的缺陷检测模块
将重复代码合并，只抽象重构方法的差异
"""

import os
import logging
import cv2
import numpy as np
from abc import ABC, abstractmethod
from typing import Tuple, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


def imread_chinese(img_path, flags=cv2.IMREAD_COLOR):
    """
    支持中文路径的图像读取函数

    参数:
        img_path: 图像路径（支持中文）
        flags: 读取模式 (cv2.IMREAD_COLOR, cv2.IMREAD_GRAYSCALE等)

    返回:
        图像数组，失败返回None
    """
    try:
        # 使用numpy读取文件字节流
        img_array = np.fromfile(img_path, dtype=np.uint8)
        # 解码图像
        img = cv2.imdecode(img_array, flags)
        return img
    except Exception as e:
        logger.error(f"读取图像失败: {img_path}, 错误: {e}")
        return None


def imwrite_chinese(img_path: str, img: np.ndarray) -> bool:
    """支持中文路径的图像写入函数"""
    try:
        ext = os.path.splitext(img_path)[1]
        success, buf = cv2.imencode(ext, img)
        if not success:
            return False
        np.array(buf).tofile(img_path)
        return True
    except Exception as e:
        logger.error(f"保存图像失败: {img_path}, 错误: {e}")
        return False


class Reconstructor(ABC):
    """重构器基类 - 只关注图像重构这一步"""

    @abstractmethod
    def reconstruct(self, image: np.ndarray, model_path: str,
                   progress_callback: Optional[Callable] = None) -> np.ndarray:
        """
        重构图像

        参数:
            image: 输入图像 (H, W) 灰度图，已归一化到 [0, 1]
            model_path: 模型文件路径
            progress_callback: 进度回调函数

        返回:
            reconstructed: 重构后的图像 (H, W)，范围 [0, 1]
        """
        pass


class DictionaryReconstructor(Reconstructor):
    """字典重构器"""

    def reconstruct(self, image: np.ndarray, model_path: str,
                   progress_callback: Optional[Callable] = None) -> np.ndarray:
        """使用字典学习进行重构（与原始代码逻辑一致）"""
        import pickle
        import warnings

        # 加载字典文件
        with open(model_path, 'rb') as f:
            data = pickle.load(f)

        # 提取字典学习器
        if isinstance(data, dict):
            dict_learner = data.get('dict_learner')
            # 禁用多进程（避免打包后的问题）
            if hasattr(dict_learner, 'n_jobs'):
                dict_learner.n_jobs = 1
            if hasattr(dict_learner, '_n_jobs'):
                dict_learner._n_jobs = 1
        else:
            raise ValueError("字典文件格式错误，需要包含 dict_learner")

        # 使用字典学习器的 transform 方法进行稀疏编码
        # 这是原始代码的方式，与直接使用 orthogonal_mp 不同
        y = image.astype(np.float32, copy=False).ravel()

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            # 使用 dict_learner.transform() 而不是 orthogonal_mp()
            sparse_code = dict_learner.transform(y.reshape(1, -1))

        # 重构
        reconstructed = dict_learner.components_.T @ sparse_code.T
        reconstructed = reconstructed.ravel().reshape(image.shape)

        if progress_callback:
            progress_callback(1, 1)

        return reconstructed


class AutoencoderReconstructor(Reconstructor):
    """自编码器重构器"""

    def __init__(self):
        """初始化时缓存模型，避免每次重构都重新加载"""
        self._model = None
        self._device = None
        self._model_path_cache = None

    def reconstruct(self, image: np.ndarray, model_path: str,
                   progress_callback: Optional[Callable] = None) -> np.ndarray:
        """使用卷积自编码器进行重构"""
        import torch
        import torch.nn as nn

        # 定义模型结构（与原始训练代码一致）
        class ConvAutoencoder(nn.Module):
            def __init__(self, input_height, input_width):
                super().__init__()
                self.input_height = input_height
                self.input_width = input_width

                self.enc_conv = nn.Sequential(
                    nn.Conv2d(1, 32, 3, 2, 1), nn.ReLU(),
                    nn.Conv2d(32, 64, 3, 2, 1), nn.ReLU(),
                    nn.Conv2d(64, 128, 3, 2, 1), nn.ReLU(),
                )

                # 用 dummy 推导 latent_h/latent_w 和 flatten_dim
                with torch.no_grad():
                    dummy = torch.zeros(1, 1, input_height, input_width)
                    out = self.enc_conv(dummy)
                    self.latent_h, self.latent_w = out.shape[2], out.shape[3]
                    self.flatten_dim = out.numel()  # = 128*latent_h*latent_w

                self.encoder = nn.Sequential(
                    self.enc_conv,
                    nn.Flatten(),
                    nn.Linear(self.flatten_dim, 1024),
                    nn.ReLU()
                )

                self.decoder_fc = nn.Sequential(
                    nn.Linear(1024, self.flatten_dim),
                    nn.ReLU()
                )

                self.decoder_conv = nn.Sequential(
                    nn.ConvTranspose2d(128, 64, 3, 2, 1, output_padding=1), nn.ReLU(),
                    nn.ConvTranspose2d(64, 32, 3, 2, 1, output_padding=1), nn.ReLU(),
                    nn.ConvTranspose2d(32, 1, 3, 2, 1, output_padding=1),
                    nn.Sigmoid()
                )

            def forward(self, x):
                z = self.encoder(x)
                z = self.decoder_fc(z)
                z = z.view(-1, 128, self.latent_h, self.latent_w)
                out = self.decoder_conv(z)

                if out.shape[2:] != (self.input_height, self.input_width):
                    out = out[:, :, :self.input_height, :self.input_width]
                return out

        # 如果模型未加载或路径变化，则加载模型
        if self._model is None or self._model_path_cache != model_path:
            self._device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

            # 使用输入图像的尺寸初始化模型（与训练时一致）
            input_height, input_width = image.shape
            self._model = ConvAutoencoder(input_height=input_height, input_width=input_width).to(self._device)

            # 修复: 添加 weights_only=False 以兼容新版本 PyTorch
            # 在 PyTorch 2.0+ 中，默认 weights_only=True 会导致加载旧模型时崩溃
            try:
                self._model.load_state_dict(torch.load(model_path, map_location=self._device, weights_only=False))
            except TypeError:
                # 兼容旧版本 PyTorch（不支持 weights_only 参数）
                self._model.load_state_dict(torch.load(model_path, map_location=self._device))

            self._model.eval()
            self._model_path_cache = model_path

        # 转换为张量
        image_tensor = torch.from_numpy(image).float().unsqueeze(0).unsqueeze(0).to(self._device)

        # 推理
        with torch.no_grad():
            reconstructed_tensor = self._model(image_tensor)

        # 转回numpy
        reconstructed = reconstructed_tensor.squeeze().cpu().numpy()

        if progress_callback:
            progress_callback(1, 1)

        return reconstructed


def enhance_image_clahe(image: np.ndarray, clip_limit: float = 4.5,
                       tile_size: int = 8) -> np.ndarray:
    """CLAHE增强"""
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
    return clahe.apply(image)


def postprocess_mask(binary_mask: np.ndarray, open_k: int = 3,
                    open_iter: int = 1, min_area: int = 15) -> np.ndarray:
    """形态学后处理"""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k))
    opened = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, kernel, iterations=open_iter)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    filtered = np.zeros_like(opened)

    for i in range(1, num_labels):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            filtered[labels == i] = 255

    return filtered


def detect_defects_unified(
    image_path: str,
    model_path: str,
    ref_path: str,
    reconstructor: Reconstructor,
    clahe_params: Optional[Dict[str, Any]] = None,
    post_cfg: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    crop_width: int = 500,
    crop_height: int = 250
) -> Tuple[np.ndarray, np.ndarray, int, np.ndarray]:
    """
    统一的缺陷检测函数（与原始逻辑一致：切分成8块逐块处理）
    """
    if post_cfg is None:
        post_cfg = {"open_k": 3, "open_iter": 1, "min_area": 15}

    # 1. 读取原始图像
    original_gray = imread_chinese(image_path, cv2.IMREAD_GRAYSCALE)
    if original_gray is None:
        raise ValueError(f"无法读取图像：{image_path}")

    # 2. CLAHE增强（可选）
    if clahe_params is not None:
        detect_gray = enhance_image_clahe(
            original_gray,
            clip_limit=clahe_params.get("clip_limit", 4.5),
            tile_size=clahe_params.get("tile_size", 8)
        )
    else:
        detect_gray = original_gray

    H, W = detect_gray.shape

    # 3. 切分图像为8块（与原始代码一致）
    if W < 4000:
        raise ValueError(f"图像宽度不足 4000，当前为 {W}")
    if H < crop_height:
        raise ValueError(f"图像高度不足 {crop_height}，当前为 {H}")

    xs = [0, 500, 1000, 1500, 2000, 2500, 3000, 3500]
    crops = []
    for x0 in xs:
        crop = detect_gray[0:crop_height, x0:x0+crop_width]
        crops.append(crop)

    # 4. 加载参考图像
    ref_gray = imread_chinese(ref_path, cv2.IMREAD_GRAYSCALE)
    if ref_gray is None:
        raise ValueError(f"无法读取参考图像：{ref_path}")

    # 5. 创建完整掩膜和残差图
    full_mask = np.zeros((H, W), dtype=np.uint8)
    full_residual = np.zeros((H, W), dtype=np.float32)

    # 6. 逐块检测
    total_crops = len(crops)
    for i, (crop, x0) in enumerate(zip(crops, xs)):
        # 归一化当前块
        crop_f = crop.astype(np.float32)
        ref_f = ref_gray.astype(np.float32)

        # 对齐均值和方差
        ref_mean = float(np.mean(ref_f))
        ref_std = float(np.std(ref_f))
        crop_mean = float(np.mean(crop_f))
        crop_std = float(np.std(crop_f))
        if crop_std < 1e-10:
            crop_std = 1.0
        norm = (ref_std / crop_std) * (crop_f - crop_mean) + ref_mean

        # 深度学习算法需要额外的 min-max 归一化（与原始代码一致）
        if isinstance(reconstructor, AutoencoderReconstructor):
            norm = (norm - norm.min()) / (norm.max() - norm.min() + 1e-10)
        else:
            norm = norm / 255.0  # 字典学习直接除以255

        # 重构当前块
        reconstructed = reconstructor.reconstruct(norm, model_path)

        # 计算残差
        residual_raw = np.abs(norm - reconstructed)

        # 应用误差阈值抑制（深度学习算法特有，字典学习不需要）
        if isinstance(reconstructor, AutoencoderReconstructor):
            error_threshold = 0.08  # 原始代码中的阈值
            residual_suppressed = residual_raw.copy()
            residual_suppressed[residual_suppressed <= error_threshold] = 0
        else:
            residual_suppressed = residual_raw

        # 归一化残差到 0-255
        residual_max = residual_suppressed.max()
        if residual_max > 1e-12:
            prob = (residual_suppressed / residual_max) * 255.0
        else:
            prob = np.zeros_like(residual_suppressed)
        prob = prob.astype(np.uint8)

        # Otsu 二值化
        _, binary = cv2.threshold(prob, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 后处理
        binary = postprocess_mask(
            binary,
            min_area=post_cfg.get("min_area", 15),
            open_k=post_cfg.get("open_k", 3),
            open_iter=post_cfg.get("open_iter", 1)
        )

        # 填充到完整掩膜和残差图
        full_mask[0:crop_height, x0:x0+crop_width] = binary
        # 使用归一化后的prob而不是residual_raw，以便热力图显示更清晰
        full_residual[0:crop_height, x0:x0+crop_width] = prob

        # 更新进度
        if progress_callback is not None:
            progress_callback(i + 1, total_crops)

    # 7. 计算缺陷总面积
    defect_area = int(np.sum(full_mask > 0))

    # 8. 裁剪到检测高度（只保留检测区域）
    original_gray = original_gray[:crop_height, :]
    full_mask = full_mask[:crop_height, :]
    full_residual = full_residual[:crop_height, :]

    # 9. 生成可视化结果（根据算法类型使用不同颜色）
    result_rgb = cv2.cvtColor(original_gray, cv2.COLOR_GRAY2RGB)

    # 字典重构用蓝色，深度学习用红色
    if isinstance(reconstructor, AutoencoderReconstructor):
        result_rgb[full_mask > 0] = (255, 0, 0)
    else:
        result_rgb[full_mask > 0] = (0, 0, 255)

    return result_rgb, full_mask, defect_area, full_residual


# 便捷函数
def detect_with_dictionary(image_path: str, model_path: str, ref_path: str,
                          clahe_params=None, post_cfg=None, progress_callback=None):
    """使用字典重构进行检测"""
    reconstructor = DictionaryReconstructor()
    return detect_defects_unified(
        image_path, model_path, ref_path, reconstructor,
        clahe_params, post_cfg, progress_callback
    )


def detect_with_autoencoder(image_path: str, model_path: str, ref_path: str,
                           clahe_params=None, post_cfg=None, progress_callback=None):
    """使用自编码器进行检测"""
    reconstructor = AutoencoderReconstructor()
    return detect_defects_unified(
        image_path, model_path, ref_path, reconstructor,
        clahe_params, post_cfg, progress_callback
    )
