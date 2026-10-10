@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title DroidExtend

net session >nul 2>&1
if errorlevel 1 (
  echo 正在请求管理员权限 ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo === 安装虚拟显示器驱动（需要管理员）===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\vdd_install.ps1"
echo.
echo 安装日志： tools\vdd\install.log
pause
