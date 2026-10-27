@echo off
chcp 65001 >nul
title 学习通作业面板
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo 没找到 Python，请先安装 Python 3.9+ 并加入 PATH。
  pause
  exit /b 1
)

echo 正在启动面板…
echo.
%PY% "%~dp0panel\app.py"
echo.
echo 面板已退出。按任意键关闭。
pause >nul
