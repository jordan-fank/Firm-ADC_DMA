# -*- coding: utf-8 -*-
"""
黑色启动动画界面
"""
from PyQt5 import QtWidgets, QtGui, QtCore
from utils.resource_utils import resource_path


class AnimatedSplash(QtWidgets.QWidget):
    finished = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SplashRoot")
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        self.card = QtWidgets.QFrame(self)
        self.card.setObjectName("SplashCard")
        self.card.setStyleSheet("""
            QFrame#SplashCard{
                background:#0B0B0B;
                border-radius:18px;
                border:1px solid #222;
            }
            QLabel{ color:white; }
        """)

        card_layout = QtWidgets.QVBoxLayout(self.card)
        card_layout.setContentsMargins(32, 32, 32, 24)

        self.labelAnim = QtWidgets.QLabel(self.card)
        self.labelAnim.setAlignment(QtCore.Qt.AlignCenter)
        self.labelAnim.setMinimumSize(480, 270)
        card_layout.addWidget(self.labelAnim)

        self.labelTitle = QtWidgets.QLabel("YUANTAI · VISION", self.card)
        self.labelTitle.setAlignment(QtCore.Qt.AlignCenter)
        self.labelTitle.setStyleSheet("font-size:18pt; font-weight:700;")
        card_layout.addWidget(self.labelTitle)

        self.labelSub = QtWidgets.QLabel("Initializing deep wall-imaging engine...", self.card)
        self.labelSub.setAlignment(QtCore.Qt.AlignCenter)
        self.labelSub.setStyleSheet("color:#AAAAAA; font-size:10pt;")
        card_layout.addWidget(self.labelSub)

        self.progress = QtWidgets.QProgressBar(self.card)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setStyleSheet("""
            QProgressBar{background:#111;border-radius:3px;}
            QProgressBar::chunk{background:#4F46E5;border-radius:3px;}
        """)
        card_layout.addWidget(self.progress)

        layout.addWidget(self.card)

        gif_path = resource_path("splash.gif")
        jpg_path = resource_path("1.jpg")
        self.movie = None
        if QtCore.QFile.exists(gif_path):
            self.movie = QtGui.QMovie(gif_path)
            self.labelAnim.setMovie(self.movie)
            self.movie.start()
        else:
            pix = QtGui.QPixmap(jpg_path)
            if not pix.isNull():
                self.labelAnim.setPixmap(
                    pix.scaled(self.labelAnim.minimumSize(),
                               QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
                )

        self.opacity_effect = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.anim = QtCore.QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self.anim.setDuration(4000)
        self.anim.setStartValue(0.0)
        self.anim.setKeyValueAt(0.2, 1.0)
        self.anim.setKeyValueAt(0.8, 1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QtCore.QEasingCurve.InOutQuad)
        self.anim.finished.connect(self._on_finished)

        self._center_on_screen()

    def _center_on_screen(self):
        screen = QtWidgets.QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.resize(640, 360)
        self.move(geo.center().x() - self.width() // 2,
                  geo.center().y() - self.height() // 2)

    def start(self):
        """开始播放淡入-淡出动画。"""
        self.anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _on_finished(self):
        self.finished.emit()
        self.close()
