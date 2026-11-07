@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title DroidExtend

net session >nul 2>&1
if errorlevel 1 goto elevate
goto install

:elevate
echo 正在请求管理员权限 ...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
if not "%errorlevel%"=="0" (
  echo.
  echo 没能启动 PowerShell 去请求管理员权限 —— 这台机器的 Windows PowerShell 可能缺失或损坏。
  echo 修法：在「管理员: 命令提示符」里依次运行
  echo     sfc /scannow
  echo     DISM /Online /Cleanup-Image /RestoreHealth
  echo 也可以直接右键本文件、选「以管理员身份运行」。
  pause
)
exit /b

:install
echo === 安装虚拟显示器驱动（需要管理员）===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\vdd_install.ps1"
if not "%errorlevel%"=="0" (
  echo.
  echo 安装脚本没有正常结束（退出码 %errorlevel%）。
  echo 如果上面一行输出都没有，多半是 Windows PowerShell 组件坏了，修法同前：
  echo     sfc /scannow
  echo     DISM /Online /Cleanup-Image /RestoreHealth
  echo 或者安装 PowerShell 7（https://aka.ms/powershell）后再试。
)
echo.
echo 安装日志： tools\vdd\install.log
pause
