# -*- coding: utf-8 -*-
"""
AppController 负责应用的高层控制逻辑：
- 创建主窗口 MainWindow
- 创建启动动画 AnimatedSplash
- 负责两者之间的信号连接
后期如果要加入：
  - 登录窗口
  - 多文档/多窗口管理
  - 全局设置/插件系统
优先在这里扩展，而不是去改 main.py。
"""

import logging
from typing import Optional

from PyQt5 import QtCore, QtWidgets

from ui.main_window import MainWindow, get_output_dir
from ui.splash_screen import AnimatedSplash
from core.http_server import HttpServerWorker
from config.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class AppController(QtCore.QObject):
    """
    应用控制器：
    - 持有 QApplication 引用
    - 管理主窗体和启动动画
    """

    def __init__(self, app: QtWidgets.QApplication, parent=None):
        super().__init__(parent)
        self._app = app
        self._main_window: Optional[MainWindow] = None
        self._splash: Optional[AnimatedSplash] = None
        self._http_server: Optional[HttpServerWorker] = None

    # ======== 对外唯一入口 ========
    def start(self):
        """
        启动应用：
        1.创建主窗体
        2.启动 HTTP 服务器
        3.创建 splash
        4.连接信号
        5.显示 splash 并开始动画
        """
        self._create_main_window()
        self._start_http_server()
        self._create_splash()
        self._connect_signals()

        # 显示启动动画；动画结束后会自动显示主窗口
        self._splash.show()
        self._splash.start()

    # ======== 内部步骤拆分 ========
    def _create_main_window(self):
        """创建并初始化主窗体。"""
        self._main_window = MainWindow()
        # 从配置读取窗口大小
        config = get_config_manager()
        width = config.get('window.width', 1360)
        height = config.get('window.height', 800)
        self._main_window.resize(width, height)

    def _create_splash(self):
        """创建启动动画界面。"""
        self._splash = AnimatedSplash()
        self._main_window._splash = self._splash

    def _connect_signals(self):
        """集中管理信号槽,方便维护。"""
        # 启动动画结束 -> 最大化显示主窗体
        self._splash.finished.connect(self._main_window.showMaximized)

        # 应用退出前 -> 停止HTTP服务器
        self._app.aboutToQuit.connect(self._stop_http_server)

        # 主窗体销毁 -> 退出应用
        self._main_window.destroyed.connect(self._app.quit)

    def _start_http_server(self):
        """启动HTTP服务器，提供output文件夹的Web访问"""
        output_dir = get_output_dir()

        # 从配置读取HTTP端口
        config = get_config_manager()
        port = config.get('http_server.port', 8000)

        self._http_server = HttpServerWorker(output_dir, port=port)

        # 连接服务器启动成功信号
        self._http_server.server_started.connect(self._on_server_started)

        # 连接服务器启动失败信号
        self._http_server.server_error.connect(self._on_server_error)

        self._http_server.start()

    def _on_server_started(self, server_url: str, port: int):
        """
        HTTP服务器启动成功的回调

        Args:
            server_url: 服务器URL
            port: 实际使用的端口号
        """
        # 更新主窗口的系统状态显示
        if self._main_window:
            self._main_window.update_server_status(True, server_url, port)

    def _on_server_error(self, error_msg: str):
        """
        HTTP服务器启动失败的回调

        Args:
            error_msg: 错误信息
        """
        # 更新主窗口的系统状态显示为离线
        if self._main_window:
            self._main_window.update_server_status(False)

    def _stop_http_server(self):
        """停止HTTP服务器"""
        if self._http_server:
            self._http_server.stop()
            if not self._http_server.wait(2000):
                logger.warning("[HTTP服务器] 未在2秒内停止，强制终止")
                self._http_server.terminate()
