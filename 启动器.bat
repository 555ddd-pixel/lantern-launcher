@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Lantern Launcher
where python >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Python，请先安装 Python 3.9 或更高版本。
  echo 下载地址：https://www.python.org/downloads/
  pause
  exit /b 1
)
python launcher.py
if errorlevel 1 pause
