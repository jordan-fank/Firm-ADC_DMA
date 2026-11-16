$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "[1/3] 清理旧打包目录..."
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

Write-Host "[2/3] 开始打包..."
pyinstaller jsyt.spec

Write-Host "[3/3] 打包完成。"
Write-Host "输出目录：dist\源泰上位机"
