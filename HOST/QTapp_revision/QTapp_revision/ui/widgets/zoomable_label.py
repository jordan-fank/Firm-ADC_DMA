"""
可缩放的图像标签控件
支持鼠标滚轮缩放、鼠标拖动和自动适配视图大小
"""
from PyQt5 import QtWidgets, QtCore, QtGui


class ZoomableLabel(QtWidgets.QLabel):
    """
    支持缩放和拖动的图像标签控件：
    - 默认 fit-to-view（适配视图大小）
    - 滚轮缩放
    - 鼠标左键拖动
    - Shift+滚轮左右滚动
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setScaledContents(False)

        self._orig_pix = None
        self._scale = 1.0
        self._fit_scale = 1.0
        self._min_scale = 0.1
        self._max_scale = 20.0

        # 拖动相关
        self._dragging = False
        self._last_pos = QtCore.QPoint()

    def sizeHint(self):
        if self._orig_pix is None:
            vp = self.parentViewport()
            if vp is not None and vp.width() > 0 and vp.height() > 0:
                return vp.size()
            return QtCore.QSize(640, 360)
        return super().sizeHint()

    def minimumSizeHint(self):
        return self.sizeHint()

    def showEvent(self, event):
        super().showEvent(event)
        self.ensure_empty_canvas_size()

    def ensure_empty_canvas_size(self):
        """Keep an empty preview canvas from collapsing to a tiny QLabel."""
        if self._orig_pix is not None:
            return
        vp = self.parentViewport()
        if vp is None:
            return
        size = vp.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        self.setMinimumSize(size)
        self.resize(size)
        self.updateGeometry()

    def setPixmap(self, pix: QtGui.QPixmap):
        """设置图像并自动适配视图"""
        self._orig_pix = pix
        self._scale = 1.0
        self.setMinimumSize(0, 0)
        super().setPixmap(pix)
        self.fit_to_view()

    def clear(self):
        """清除图像，包括内部缓存的原始图像"""
        self._orig_pix = None
        self._scale = 1.0
        self._fit_scale = 1.0
        super().clear()
        self.ensure_empty_canvas_size()

    def fit_to_view(self):
        """按当前 scrollArea viewport 尺寸等比缩放铺满（不裁切）"""
        if self._orig_pix is None:
            return
        vp = self.parentViewport()
        if vp is None:
            return
        vw, vh = vp.width(), vp.height()
        pw, ph = self._orig_pix.width(), self._orig_pix.height()
        if pw == 0 or ph == 0:
            return
        s = min(vw / pw, vh / ph)
        s = max(s, self._min_scale)
        self._fit_scale = s
        self._scale = 1.0
        self._apply_scale()

    def parentViewport(self):
        """查找父级 ScrollArea 的 viewport"""
        p = self.parent()
        while p is not None:
            if isinstance(p, QtWidgets.QAbstractScrollArea):
                return p.viewport()
            p = p.parent()
        return None

    def parentScrollArea(self):
        """查找父级 ScrollArea"""
        p = self.parent()
        while p is not None:
            if isinstance(p, QtWidgets.QAbstractScrollArea):
                return p
            p = p.parent()
        return None

    def _apply_scale(self):
        """应用缩放"""
        if self._orig_pix is None:
            return
        s = self._fit_scale * self._scale
        w = int(self._orig_pix.width() * s)
        h = int(self._orig_pix.height() * s)

        scaled = self._orig_pix.scaled(
            w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        super().setPixmap(scaled)

        self.resize(scaled.size())
        self.adjustSize()

    def wheelEvent(self, event: QtGui.QWheelEvent):
        """鼠标滚轮缩放"""
        if event.modifiers() & QtCore.Qt.ShiftModifier:
            # Shift+滚轮：水平滚动（由父容器处理）
            event.ignore()
            return

        if self._orig_pix is None:
            event.ignore()
            return

        # 缩放因子
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15

        new_scale = self._scale * factor
        new_scale = max(self._min_scale, min(self._max_scale, new_scale))
        self._scale = new_scale
        self._apply_scale()
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        """鼠标按下事件 - 开始拖动"""
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self._last_pos = event.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        """鼠标移动事件 - 拖动图像"""
        if self._dragging:
            scroll_area = self.parentScrollArea()
            if scroll_area:
                # 计算移动距离
                delta = event.pos() - self._last_pos

                # 更新滚动条位置
                h_bar = scroll_area.horizontalScrollBar()
                v_bar = scroll_area.verticalScrollBar()

                h_bar.setValue(h_bar.value() - delta.x())
                v_bar.setValue(v_bar.value() - delta.y())

                self._last_pos = event.pos()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        """鼠标释放事件 - 结束拖动"""
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            self.setCursor(QtCore.Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)
