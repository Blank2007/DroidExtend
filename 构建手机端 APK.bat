@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title DroidExtend

set "PY="
for %%P in (
  "C:\Python314\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) do if exist %%P if not defined PY set "PY=%%~P"
if not defined PY (
  for /f "delims=" %%W in ('where python 2^>nul') do if not defined PY set "PY=%%W"
)
if not defined PY (
  echo   找不到 Python，请先安装 Python 3.9 或更高版本：https://www.python.org/downloads/windows/
  echo   安装时请勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)

"%PY%" "%~dp0android\build.py" %*
echo.
echo 输出： dist\DroidExtend.apk
pause
