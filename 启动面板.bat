@echo off
title Chaoxing Panel
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set "PY="

python -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run

py -3.10 -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3.10"
if defined PY goto run

py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

echo Python 3.9+ not found.
echo Download: https://www.python.org/downloads/
echo Remember to check "Add python.exe to PATH" during install.
pause
exit /b 1

:run
%PY% "%~dp0bootstrap.py"
if errorlevel 1 pause
exit /b 0
