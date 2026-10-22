@echo off
chcp 65001 >nul
setlocal

rem 启动 Understand Anything 面板，展示本项目的知识图谱
rem 浏览器会自动打开带 token 的地址；关闭此窗口即停止服务

set "UA_CACHE=%USERPROFILE%\.claude\plugins\cache\understand-anything\understand-anything"
set "PLUGIN_DIR="
for /f "delims=" %%D in ('dir /b /ad /o-d "%UA_CACHE%" 2^>nul') do (
  if not defined PLUGIN_DIR if exist "%UA_CACHE%\%%D\packages\dashboard\node_modules\.bin\vite.cmd" set "PLUGIN_DIR=%UA_CACHE%\%%D"
)

if not defined PLUGIN_DIR (
  echo 未找到 understand-anything 插件的 dashboard，请确认插件已安装。
  pause
  exit /b 1
)

set "GRAPH_DIR=%~dp0"
cd /d "%PLUGIN_DIR%\packages\dashboard"

echo 正在启动面板，浏览器将自动打开...
echo 关闭此窗口即停止服务。
echo.

call node_modules\.bin\vite.cmd --host 127.0.0.1

pause
