# -*- coding: utf-8 -*-
import sys
import multiprocessing
from PyQt5 import QtWidgets, QtGui

from core.app_controller import AppController
from utils.resource_utils import resource_path


def main():
    """
    程序入口函数：
    - 创建 QApplication
    - 设置全局图标
    - 创建并启动 AppController
    """
    app = QtWidgets.QApplication(sys.argv)

    # 全局应用图标
    icon = QtGui.QIcon(resource_path("3.png"))
    if not icon.isNull():
        app.setWindowIcon(icon)

    # 高层应用控制器，负责主窗体与启动动画
    controller = AppController(app)
    controller.start()

    # Qt 事件循环
    sys.exit(app.exec_())


if __name__ == "__main__":
    # Windows多进程支持（PyInstaller打包必需）
    # 防止子进程重复启动主程序导致无限递归
    multiprocessing.freeze_support()
    
    main()
