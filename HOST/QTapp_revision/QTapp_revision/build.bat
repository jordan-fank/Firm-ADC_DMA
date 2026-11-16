@echo off
setlocal

cd /d "%~dp0"

echo [1/3] 清理旧打包目录...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/3] 开始打包...
pyinstaller jsyt.spec
if errorlevel 1 (
    echo 打包失败，请检查上方日志。
    pause
    exit /b 1
)

echo [3/3] 打包完成。
echo 输出目录：dist\源泰上位机
pause
