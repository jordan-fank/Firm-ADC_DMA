# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

added_files = [
    ("AppMain.ui", "."),
    ("1.jpg", "."),
    ("3.png", "."),
    ("jsyt.ico", "."),
    ("config", "config"),
    ("models", "models"),
    ("templates", "templates"),
]

excluded_modules = [
    "scipy",
    "matplotlib",
    "PIL",
    "Pillow",
    "pytest",
    "tkinter",
    "IPython",
    "jedi",
    "pandas",
    "sklearn",
    "torch",
    "tensorflow",
    "onnx",
    "PyQt5.Qt3DAnimation",
    "PyQt5.Qt3DCore",
    "PyQt5.Qt3DExtras",
    "PyQt5.Qt3DInput",
    "PyQt5.Qt3DLogic",
    "PyQt5.Qt3DRender",
    "PyQt5.QtBluetooth",
    "PyQt5.QtDBus",
    "PyQt5.QtDesigner",
    "PyQt5.QtHelp",
    "PyQt5.QtLocation",
    "PyQt5.QtMultimedia",
    "PyQt5.QtMultimediaWidgets",
    "PyQt5.QtNetwork",
    "PyQt5.QtNfc",
    "PyQt5.QtOpenGL",
    "PyQt5.QtPositioning",
    "PyQt5.QtPrintSupport",
    "PyQt5.QtQml",
    "PyQt5.QtQuick",
    "PyQt5.QtQuick3D",
    "PyQt5.QtQuickWidgets",
    "PyQt5.QtSql",
    "PyQt5.QtSvg",
    "PyQt5.QtTest",
    "PyQt5.QtWebChannel",
    "PyQt5.QtWebEngine",
    "PyQt5.QtWebEngineCore",
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtWebSockets",
    "PyQt5.QtXml",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "PyQt5.QtSerialPort",
        "PyQt5.uic",
        "cv2",
        "numpy",
        "serial.tools.list_ports",
        "pymodbus.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


def keep_dist_entry(entry):
    name = entry[0].replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    if "cv2/opencv_videoio_ffmpeg" in name:
        return False
    if name.startswith("pyqt5/qt5/translations/"):
        return False
    if name.startswith("pyqt5/qt5/plugins/platforms/"):
        return base == "qwindows.dll"
    if name.startswith("pyqt5/qt5/plugins/imageformats/"):
        return base in {"qgif.dll", "qico.dll", "qjpeg.dll"}
    if name.startswith("pyqt5/qt5/plugins/") and not name.startswith("pyqt5/qt5/plugins/imageformats/") and not name.startswith("pyqt5/qt5/plugins/platforms/"):
        return False
    if name.startswith("pyqt5/qt5/bin/") and base in {
        "opengl32sw.dll",
        "qt5quick.dll",
        "qt5qml.dll",
        "qt5qmlmodels.dll",
        "qt5dbus.dll",
        "qt5network.dll",
        "qt5websockets.dll",
    }:
        return False
    return True


a.binaries = [entry for entry in a.binaries if keep_dist_entry(entry)]
a.datas = [entry for entry in a.datas if keep_dist_entry(entry)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="YuantaiUpper_onefile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="jsyt.ico",
)
