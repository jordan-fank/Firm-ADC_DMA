# -*- coding: utf-8 -*-
"""
分割控制器：负责缺陷检测和分割分析

职责：
1. 分割界面初始化和UI控件管理
2. 图像浏览和加载
3. 缺陷检测算法执行（字典学习/深度学习）
4. 检测结果显示和可视化
5. 结果导出（按时间戳保存到output文件夹）
6. 参数管理和重置
"""

import os
import logging
import numpy as np
import cv2
from datetime import datetime
from typing import Optional, Tuple, Callable
from PyQt5 import QtWidgets, QtGui, QtCore

from utils.resource_utils import resource_path
from ui.widgets.zoomable_label import ZoomableLabel
from algorithms.defect_detection_unified import detect_with_dictionary, detect_with_autoencoder, imread_chinese, imwrite_chinese
from core.env_check import check_pytorch_available, get_device_info

logger = logging.getLogger(__name__)


class NonDraggableZoomableLabel(ZoomableLabel):
    """不支持拖拽的 ZoomableLabel（只保留缩放和滚动条功能）"""

    def mousePressEvent(self, event):
        """禁用鼠标按下事件"""
        event.ignore()

    def mouseMoveEvent(self, event):
        """禁用鼠标移动事件"""
        event.ignore()

    def mouseReleaseEvent(self, event):
        """禁用鼠标释放事件"""
        event.ignore()


class SegmentationController:
    """
    分割控制器：负责缺陷检测和分割分析

    架构说明：
        将分割功能从MainWindow中分离，使用控制器模式管理所有分割相关的业务逻辑。
        MainWindow只负责UI协调，具体的检测、显示、保存等操作由本控制器处理。
    """

    def __init__(self, parent, ui, config, get_output_dir_func: Callable):
        """
        初始化分割控制器

        Args:
            parent: 父窗口（MainWindow实例）
            ui: UI对象（通常是MainWindow自身）
            config: 配置管理器
            get_output_dir_func: 获取输出目录的函数
        """
        self.parent = parent
        self.ui = ui
        self.config = config
        self.get_output_dir = get_output_dir_func

        # 检测结果缓存
        self._last_result_rgb: Optional[np.ndarray] = None  # 2D可视化
        self._last_mask: Optional[np.ndarray] = None        # 缺陷掩膜
        self._last_area: int = 0                             # 缺陷面积
        self._last_heatmap: Optional[np.ndarray] = None     # 3D热力图

        # 检查深度学习是否可用
        self._dl_available, _ = check_pytorch_available()

        logger.info("[分割控制器] 初始化完成")

    def initialize(self):
        """初始化分割界面（替换标签、连接信号、设置默认值）"""
        logger.info("[分割控制器] 开始初始化分割界面...")

        # 1. 替换标签为可缩放标签
        self._replace_with_zoomable_labels()

        # 2. 连接信号
        self._connect_signals()

        # 3. 初始化算法选择器
        self._initialize_algorithm_selector()

        # 4. 初始化CLAHE开关
        self._initialize_clahe_toggle()

        logger.info("[分割控制器] 分割界面初始化完成")

    # ========== 公共接口 ==========

    def browse_image(self):
        """浏览并加载待检测图像"""
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.parent,
            "选择待检测图像",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All Files (*.*)"
        )
        if not file_path:
            return

        # 设置路径到输入框
        if hasattr(self.ui, "lineEditImagePath"):
            self.ui.lineEditImagePath.setText(file_path)

        # 在预览区显示原始图像
        if hasattr(self.ui, "labelPreview2D"):
            self._show_image_on_label(file_path, self.ui.labelPreview2D)

        self._log(f"[分割] 已选择图像: {os.path.basename(file_path)}")

    def run_detection(self):
        """运行缺陷检测（主工作流）"""
        # 强制提交所有SpinBox的编辑值（解决直接输入数值后立即点击按钮导致值未提交的问题）
        if QtWidgets.QApplication.focusWidget():
            QtWidgets.QApplication.focusWidget().clearFocus()

        # 重置进度条
        self._update_progress(0)

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            # 1. 提取参数
            img_path, method, model_or_dict_path, ref_path, clahe_params, post_cfg = self._extract_detection_params()

            # 2. 运行检测
            result_rgb, full_mask, defect_area, full_residual = self._run_detection_algorithm(
                img_path, method, model_or_dict_path, ref_path, clahe_params, post_cfg
            )

            # 3. 显示结果
            self._display_results(result_rgb, full_mask, defect_area, full_residual)

            # 4. 自动保存最新结果（固定名称）
            try:
                self._save_latest_results(result_rgb, full_mask, full_residual)
            except Exception as e:
                import traceback
                self._log(f"[警告] 自动保存最新结果失败：{e}")
                logger.warning(f"[分割控制器] 自动保存失败: {traceback.format_exc()}")

            self._log(f"[缺陷检测] 检测完成，缺陷总面积：{defect_area} px²")

            QtWidgets.QMessageBox.information(
                self.parent, "检测完成",
                f"缺陷检测已完成！\n缺陷总面积：{defect_area} px²"
            )

        except (ValueError, FileNotFoundError) as e:
            QtWidgets.QMessageBox.warning(self.parent, "提示", str(e))
        except Exception as e:
            import traceback
            error_msg = f"缺陷检测失败：\n{e}\n{traceback.format_exc()}"
            self._log(f"[错误] {error_msg}")
            QtWidgets.QMessageBox.critical(self.parent, "错误", error_msg)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._update_progress(0)

    def export_results(self):
        """导出检测结果到output文件夹（按时间戳命名）"""
        if self._last_result_rgb is None:
            QtWidgets.QMessageBox.information(
                self.parent, "提示", "当前没有可导出的检测结果，请先运行检测。"
            )
            return

        # 获取输出目录
        output_dir = self.get_output_dir()
        if not output_dir:
            QtWidgets.QMessageBox.warning(
                self.parent, "警告", "输出目录未设置，请先在配置中设置输出目录。"
            )
            return

        try:
            from datetime import datetime

            # 直接保存到output文件夹根目录
            os.makedirs(output_dir, exist_ok=True)

            # 生成时间戳前缀
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefix = f"seg_{timestamp}"

            # 保存2D可视化结果
            result_path = os.path.join(output_dir, f"{prefix}_result.png")
            self._save_result_image(result_path, self._last_result_rgb)

            # 保存掩膜
            if self._last_mask is not None:
                mask_path = os.path.join(output_dir, f"{prefix}_mask.png")
                imwrite_chinese(mask_path, self._last_mask)

            # 保存热力图
            if self._last_heatmap is not None and self._last_heatmap.size > 0:
                heatmap_vis = self._generate_heatmap_visualization(self._last_heatmap)
                heatmap_path = os.path.join(output_dir, f"{prefix}_heatmap.png")
                imwrite_chinese(heatmap_path, heatmap_vis)

            # 保存统计信息
            info_path = os.path.join(output_dir, f"{prefix}_info.txt")
            self._save_result_info(info_path)

            QtWidgets.QMessageBox.information(
                self.parent,
                "导出成功",
                f"检测结果已导出到：\n{output_dir}\n\n文件前缀：{prefix}"
            )

            self._log(f"[导出] 检测结果已导出到：{output_dir}，前缀：{prefix}")

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            self._log(f"[错误] 导出失败：{e}\n{error_detail}")
            QtWidgets.QMessageBox.critical(self.parent, "错误", f"导出失败：\n{e}")

    def reset_parameters(self):
        """重置所有参数到默认值"""
        # CLAHE参数
        if hasattr(self.ui, "checkEnableCLAHE"):
            self.ui.checkEnableCLAHE.setChecked(True)
        if hasattr(self.ui, "spinClipLimit"):
            self.ui.spinClipLimit.setValue(4.5)
        if hasattr(self.ui, "comboTileSize"):
            self.ui.comboTileSize.setCurrentIndex(0)  # 设置为8

        # 后处理参数
        if hasattr(self.ui, "spinOpenK"):
            self.ui.spinOpenK.setValue(3)
        if hasattr(self.ui, "spinOpenIter"):
            self.ui.spinOpenIter.setValue(1)
        if hasattr(self.ui, "spinMinArea"):
            self.ui.spinMinArea.setValue(15)

        self._log("[参数重置] 所有参数已重置为默认值")
        QtWidgets.QMessageBox.information(self.parent, "提示", "参数已重置为默认值")

    def clear_results(self):
        """清空检测结果和显示"""
        self._last_result_rgb = None
        self._last_mask = None
        self._last_area = 0
        self._last_heatmap = None

        # 清空显示标签
        if hasattr(self.ui, "labelPreview2D"):
            self.ui.labelPreview2D.clear()
        if hasattr(self.ui, "labelPreview3D"):
            self.ui.labelPreview3D.clear()
        if hasattr(self.ui, "labelAreaValue"):
            self.ui.labelAreaValue.setText("0 px²")

        self._log("[分割] 检测结果已清空")

    # ========== 私有方法：初始化 ==========

    def _replace_with_zoomable_labels(self):
        """将分割页面的QLabel替换为ZoomableLabel，并包裹在ScrollArea中"""
        try:
            # 替换2D预览标签
            if hasattr(self.ui, "labelPreview2D"):
                old_label = self.ui.labelPreview2D
                parent_widget = old_label.parent()
                layout = parent_widget.layout()

                # 创建ScrollArea（参考成像界面的实现）
                scroll_area = QtWidgets.QScrollArea(parent_widget)
                scroll_area.setObjectName("scrollAreaPreview2D")
                scroll_area.setWidgetResizable(False)
                scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
                scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
                scroll_area.setAlignment(QtCore.Qt.AlignCenter)
                scroll_area.setStyleSheet(
                    "QScrollArea{background-color:#EAF4FB; border:1px solid #C6D9E6; border-radius:7px;}"
                    "QScrollArea > QWidget > QWidget{background-color:#EAF4FB;}"
                )

                # 创建ZoomableLabel（禁用拖拽），直接作为ScrollArea的widget
                new_label = NonDraggableZoomableLabel(scroll_area)
                new_label.setObjectName("labelPreview2D")
                new_label.setAlignment(QtCore.Qt.AlignCenter)
                new_label.setStyleSheet(
                    "background-color:#EAF4FB; "
                    "border:1px solid #C6D9E6; "
                    "border-radius:7px; "
                    "color:#365766; "
                    "font-weight:600;"
                )
                new_label.setSizePolicy(
                    QtWidgets.QSizePolicy.Ignored,
                    QtWidgets.QSizePolicy.Ignored
                )

                # 直接将ZoomableLabel设置为ScrollArea的widget
                scroll_area.setWidget(new_label)
                new_label.ensure_empty_canvas_size()
                scroll_area.viewport().installEventFilter(self.parent)

                # 替换原有的label
                if layout:
                    idx = layout.indexOf(old_label)
                    layout.removeWidget(old_label)
                    old_label.deleteLater()
                    layout.insertWidget(idx, scroll_area)

                self.ui.labelPreview2D = new_label
                self.ui.scrollAreaPreview2D = scroll_area
                logger.info("[分割控制器] 已替换labelPreview2D为ZoomableLabel并添加ScrollArea")

            # 替换3D预览标签
            if hasattr(self.ui, "labelPreview3D"):
                old_label = self.ui.labelPreview3D
                parent_widget = old_label.parent()
                layout = parent_widget.layout()

                # 创建ScrollArea（参考成像界面的实现）
                scroll_area = QtWidgets.QScrollArea(parent_widget)
                scroll_area.setObjectName("scrollAreaPreview3D")
                scroll_area.setWidgetResizable(False)
                scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
                scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
                scroll_area.setAlignment(QtCore.Qt.AlignCenter)
                scroll_area.setStyleSheet(
                    "QScrollArea{background-color:#EAF4FB; border:1px solid #C6D9E6; border-radius:7px;}"
                    "QScrollArea > QWidget > QWidget{background-color:#EAF4FB;}"
                )

                # 创建ZoomableLabel（禁用拖拽），直接作为ScrollArea的widget
                new_label = NonDraggableZoomableLabel(scroll_area)
                new_label.setObjectName("labelPreview3D")
                new_label.setAlignment(QtCore.Qt.AlignCenter)
                new_label.setStyleSheet(
                    "background-color:#EAF4FB; "
                    "border:1px solid #C6D9E6; "
                    "border-radius:7px; "
                    "color:#365766; "
                    "font-weight:600;"
                )
                new_label.setSizePolicy(
                    QtWidgets.QSizePolicy.Ignored,
                    QtWidgets.QSizePolicy.Ignored
                )

                # 直接将ZoomableLabel设置为ScrollArea的widget
                scroll_area.setWidget(new_label)
                new_label.ensure_empty_canvas_size()
                scroll_area.viewport().installEventFilter(self.parent)

                # 替换原有的label
                if layout:
                    idx = layout.indexOf(old_label)
                    layout.removeWidget(old_label)
                    old_label.deleteLater()
                    layout.insertWidget(idx, scroll_area)

                self.ui.labelPreview3D = new_label
                self.ui.scrollAreaPreview3D = scroll_area
                logger.info("[分割控制器] 已替换labelPreview3D为ZoomableLabel并添加ScrollArea")

        except Exception as e:
            logger.error(f"[分割控制器] 替换ZoomableLabel失败: {e}")

    def _connect_signals(self):
        """连接分割页面的所有信号"""
        # 浏览图像按钮
        if hasattr(self.ui, "btnBrowseSeg"):
            self.ui.btnBrowseSeg.clicked.connect(self.browse_image)
            logger.info("[分割控制器] btnBrowseSeg 信号已连接")
        else:
            logger.warning("[分割控制器] btnBrowseSeg 不存在")

        # 运行检测按钮
        if hasattr(self.ui, "btnRun"):
            self.ui.btnRun.clicked.connect(self.run_detection)
            logger.info("[分割控制器] btnRun 信号已连接")
        else:
            logger.warning("[分割控制器] btnRun 不存在")

        # 导出结果按钮
        if hasattr(self.ui, "btnExport"):
            self.ui.btnExport.clicked.connect(self.export_results)
            logger.info("[分割控制器] btnExport 信号已连接")
        else:
            logger.warning("[分割控制器] btnExport 不存在")

        # 重置参数按钮
        if hasattr(self.ui, "btnReset"):
            self.ui.btnReset.clicked.connect(self.reset_parameters)
            logger.info("[分割控制器] btnReset 信号已连接")
        else:
            logger.warning("[分割控制器] btnReset 不存在")

        logger.info("[分割控制器] 信号连接完成")

    def _initialize_algorithm_selector(self):
        """初始化算法选择器"""
        if not hasattr(self.ui, "comboMethod"):
            return

        self.ui.comboMethod.clear()
        self.ui.comboMethod.addItem("字典学习 (Dictionary Learning)")

        if self._dl_available:
            self.ui.comboMethod.addItem("深度学习 (Autoencoder)")
            logger.info("[分割控制器] 深度学习算法可用")
        else:
            logger.warning("[分割控制器] 深度学习算法不可用（PyTorch未安装）")

        self.ui.comboMethod.setCurrentIndex(0)

    def _initialize_clahe_toggle(self):
        """初始化CLAHE开关，默认启用"""
        if hasattr(self.ui, "checkEnableCLAHE"):
            self.ui.checkEnableCLAHE.setChecked(True)

    # ========== 私有方法：参数提取 ==========

    def _extract_detection_params(self) -> Tuple:
        """
        提取检测参数

        Returns:
            (img_path, method, model_or_dict_path, ref_path, clahe_params, post_cfg)
        """
        # 1. 图像路径
        img_path = self.ui.lineEditImagePath.text().strip() if hasattr(self.ui, "lineEditImagePath") else ""
        if not img_path or not os.path.isfile(img_path):
            raise ValueError("请先选择有效的待检测图像")

        # 2. 算法选择
        method = "dictionary"
        if hasattr(self.ui, "comboMethod"):
            algo_text = self.ui.comboMethod.currentText()
            if "深度学习" in algo_text or "Autoencoder" in algo_text:
                method = "autoencoder"

        # 3. 模型/字典路径
        model_or_dict_path = self._get_model_or_dict_path(method)

        # 4. 参考图像路径（字典学习和深度学习都需要，用于均值方差对齐）
        ref_path = self._get_reference_image_path()

        # 5. CLAHE参数
        clahe_params = self._get_clahe_params()

        # 6. 后处理参数
        post_cfg = self._get_postprocess_params()

        return img_path, method, model_or_dict_path, ref_path, clahe_params, post_cfg

    def _get_model_or_dict_path(self, method: str) -> str:
        """获取模型或字典路径，优先从UI读取，否则从配置文件读取"""
        if method == "autoencoder":
            # 深度学习模型路径
            # TODO: UI中需要添加模型路径输入框
            model_path = self.config.get("segmentation.model_path", "models/autoencoder_model.pth")
            model_path = resource_path(model_path)
            if not os.path.isfile(model_path):
                raise FileNotFoundError(f"深度学习模型文件不存在：{model_path}")
            return model_path
        else:
            # 字典学习字典路径 - 优先从UI读取（用户输入的路径不经过resource_path）
            dict_path = None
            if hasattr(self.ui, "lineEditDictPath"):
                ui_path = self.ui.lineEditDictPath.text().strip()
                if ui_path:
                    dict_path = ui_path

            # 如果UI未填写，从配置文件读取
            if not dict_path:
                dict_path = self.config.get("segmentation.dictionary_path", "models/dictionary.pkl")
                dict_path = resource_path(dict_path)

            if not os.path.isfile(dict_path):
                raise FileNotFoundError(f"字典文件不存在：{dict_path}")
            return dict_path

    def _get_reference_image_path(self) -> str:
        """获取参考图像路径（字典学习需要），优先从UI读取"""
        # 优先从UI读取
        ref_path = None
        if hasattr(self.ui, "lineEditRefPath"):
            ui_path = self.ui.lineEditRefPath.text().strip()
            if ui_path:
                ref_path = resource_path(ui_path)

        # 如果UI未填写，从配置文件读取
        if not ref_path:
            ref_path = self.config.get("segmentation.reference_image", "data/reference.png")
            ref_path = resource_path(ref_path)

        if not os.path.isfile(ref_path):
            raise FileNotFoundError(f"参考图像不存在：{ref_path}")
        return ref_path

    def _get_clahe_params(self) -> Optional[dict]:
        """获取CLAHE参数，如果未启用则返回None"""
        # 检查CLAHE是否启用
        if not (hasattr(self.ui, "checkEnableCLAHE") and self.ui.checkEnableCLAHE.isChecked()):
            return None  # 返回None表示不使用CLAHE

        # 启用时返回参数字典
        clahe_params = {}

        # clip_limit
        if hasattr(self.ui, "spinClipLimit"):
            clahe_params["clip_limit"] = self.ui.spinClipLimit.value()
        else:
            clahe_params["clip_limit"] = 4.5

        # tile_size
        if hasattr(self.ui, "comboTileSize"):
            tile_text = self.ui.comboTileSize.currentText()
            tile_size = int(tile_text) if tile_text.isdigit() else 8
            clahe_params["tile_size"] = tile_size
        else:
            clahe_params["tile_size"] = 8

        return clahe_params

    def _get_postprocess_params(self) -> dict:
        """获取后处理参数"""
        post_cfg = {}

        # 形态学开运算 (修正键名以匹配 defect_detection_unified.py)
        if hasattr(self.ui, "spinOpenK"):
            post_cfg["open_k"] = self.ui.spinOpenK.value()
        else:
            post_cfg["open_k"] = 3

        if hasattr(self.ui, "spinOpenIter"):
            post_cfg["open_iter"] = self.ui.spinOpenIter.value()
        else:
            post_cfg["open_iter"] = 1

        # 最小面积过滤
        if hasattr(self.ui, "spinMinArea"):
            post_cfg["min_area"] = self.ui.spinMinArea.value()
        else:
            post_cfg["min_area"] = 15

        return post_cfg

    # ========== 私有方法：检测算法 ==========

    def _run_detection_algorithm(
        self,
        img_path: str,
        method: str,
        model_or_dict_path: str,
        ref_path: Optional[str],
        clahe_params: dict,
        post_cfg: dict
    ) -> Tuple[np.ndarray, np.ndarray, int, np.ndarray]:
        """
        运行检测算法

        Returns:
            (result_rgb, full_mask, defect_area, full_residual)
        """
        self._update_progress(10)
        self._log(f"[缺陷检测] 开始检测，算法：{method}")

        if method == "autoencoder":
            result_rgb, full_mask, defect_area, full_residual = detect_with_autoencoder(
                image_path=img_path,
                model_path=model_or_dict_path,
                ref_path=ref_path,
                clahe_params=clahe_params,
                post_cfg=post_cfg,
                progress_callback=self._update_progress
            )
        else:
            result_rgb, full_mask, defect_area, full_residual = detect_with_dictionary(
                image_path=img_path,
                model_path=model_or_dict_path,
                ref_path=ref_path,
                clahe_params=clahe_params,
                post_cfg=post_cfg,
                progress_callback=self._update_progress
            )

        return result_rgb, full_mask, defect_area, full_residual

    # ========== 私有方法：结果显示 ==========

    def _display_results(
        self,
        result_rgb: np.ndarray,
        full_mask: np.ndarray,
        defect_area: int,
        full_residual: np.ndarray
    ):
        """显示检测结果"""
        # 缓存结果
        self._last_result_rgb = result_rgb
        self._last_mask = full_mask
        self._last_area = defect_area
        self._last_heatmap = full_residual

        # 显示2D可视化
        if hasattr(self.ui, "labelPreview2D"):
            self._show_array_on_label(result_rgb, self.ui.labelPreview2D)

        # 显示3D热力图
        if hasattr(self.ui, "labelPreview3D"):
            heatmap_vis = self._generate_heatmap_visualization(full_residual)
            self._show_array_on_label(heatmap_vis, self.ui.labelPreview3D)

        # 显示缺陷面积
        if hasattr(self.ui, "labelAreaValue"):
            self.ui.labelAreaValue.setText(f"{defect_area} px²")

        self._update_progress(100)

    def _generate_heatmap_visualization(self, residual: np.ndarray) -> np.ndarray:
        """生成热力图可视化"""
        if residual is None or residual.size == 0:
            return np.zeros((100, 100, 3), dtype=np.uint8)

        # 归一化到0-255
        residual_norm = cv2.normalize(residual, None, 0, 255, cv2.NORM_MINMAX)
        residual_norm = residual_norm.astype(np.uint8)

        # 应用颜色映射
        heatmap = cv2.applyColorMap(residual_norm, cv2.COLORMAP_JET)

        return heatmap

    # ========== 私有方法：结果保存 ==========

    def _save_latest_results(
        self,
        result_rgb: np.ndarray,
        full_mask: np.ndarray,
        full_residual: np.ndarray
    ):
        """自动保存最新检测结果（固定名称）"""
        output_dir = self.get_output_dir()
        if not output_dir:
            return

        os.makedirs(output_dir, exist_ok=True)

        # 使用固定名称保存最新结果
        # 保存2D可视化结果
        result_path = os.path.join(output_dir, "latest_seg_result.png")
        self._save_result_image(result_path, result_rgb)

        # 保存掩膜
        if full_mask is not None:
            mask_path = os.path.join(output_dir, "latest_seg_mask.png")
            imwrite_chinese(mask_path, full_mask)

        # 保存热力图
        if full_residual is not None and full_residual.size > 0:
            heatmap_vis = self._generate_heatmap_visualization(full_residual)
            heatmap_path = os.path.join(output_dir, "latest_seg_heatmap.png")
            imwrite_chinese(heatmap_path, heatmap_vis)

        # 保存统计信息
        info_path = os.path.join(output_dir, "latest_seg_info.txt")
        self._save_result_info(info_path)

        self._log(f"[自动保存] 最新结果已保存到：{output_dir}")

    def _save_result_image(self, path: str, image: np.ndarray):
        """保存结果图像"""
        if image is None:
            raise ValueError("图像数据为空")

        # 确保目录存在
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # 转换为BGR格式（OpenCV格式）
        if len(image.shape) == 3 and image.shape[2] == 3:
            image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        else:
            image_bgr = image

        if not imwrite_chinese(path, image_bgr):
            raise IOError(f"无法保存图像到：{path}")

    def _save_result_info(self, base_path: str):
        """保存检测结果统计信息"""
        # 如果base_path是图像路径，转换为txt路径
        if base_path.endswith(('.png', '.jpg', '.jpeg', '.bmp')):
            base, _ = os.path.splitext(base_path)
            info_path = base + "_info.txt"
        else:
            info_path = base_path

        # 确保目录存在
        os.makedirs(os.path.dirname(info_path), exist_ok=True)

        with open(info_path, "w", encoding="utf-8") as f:
            f.write("=" * 50 + "\n")
            f.write("缺陷检测结果统计\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"缺陷总面积：{self._last_area} px²\n")
            f.write("\n")
            f.write("说明：\n")
            f.write("- 缺陷区域在结果图中以红色标注\n")
            f.write("- 热力图显示了残差分布（颜色越亮表示异常程度越高）\n")

    # ========== 私有方法：UI辅助 ==========

    def _show_image_on_label(self, img_path: str, label: QtWidgets.QLabel):
        """在标签上显示图像文件"""
        try:
            img = imread_chinese(img_path)
            if img is None:
                raise ValueError(f"无法读取图像：{img_path}")

            # 转换为RGB
            if len(img.shape) == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
            elif img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            self._show_array_on_label(img, label)

        except Exception as e:
            logger.error(f"[分割控制器] 显示图像失败：{e}")
            label.setText(f"无法显示图像\n{str(e)}")

    def _show_array_on_label(self, img_array: np.ndarray, label: QtWidgets.QLabel):
        """在标签上显示numpy数组图像"""
        try:
            if img_array is None or img_array.size == 0:
                label.setText("无图像数据")
                return

            # 确保是RGB格式
            if len(img_array.shape) == 2:
                img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)

            h, w, ch = img_array.shape
            bytes_per_line = ch * w

            # 创建QImage
            q_img = QtGui.QImage(
                img_array.data,
                w, h,
                bytes_per_line,
                QtGui.QImage.Format_RGB888
            )

            # 转换为 QPixmap 并设置到标签
            pixmap = QtGui.QPixmap.fromImage(q_img)

            if isinstance(label, ZoomableLabel):
                # ZoomableLabel 使用 setPixmap 方法
                label.setPixmap(pixmap)
            else:
                # 普通 QLabel 需要缩放
                label.setPixmap(pixmap.scaled(
                    label.size(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation
                ))

        except Exception as e:
            logger.error(f"[分割控制器] 显示数组失败：{e}")
            label.setText(f"显示失败\n{str(e)}")

    def _update_progress(self, current: int, total: int = 100):
        """
        更新进度条

        Args:
            current: 当前进度值
            total: 总进度值（默认100，表示百分比）
        """
        if hasattr(self.ui, "progressBar"):
            # 计算百分比
            percentage = int((current / total) * 100) if total > 0 else 0
            self.ui.progressBar.setValue(percentage)
            QtWidgets.QApplication.processEvents()

    def _log(self, message: str):
        """输出日志到UI和logger"""
        logger.info(message)
        if hasattr(self.parent, "log"):
            self.parent.log(message)
