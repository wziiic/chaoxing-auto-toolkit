@echo off
title Chaoxing Panel
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set "PY="

python -c "import requests,Crypto,fastapi" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto runpy

py -3.10 -c "import requests,Crypto,fastapi" >nul 2>nul
if not errorlevel 1 set "PY=py -3.10"
if defined PY goto runpy

py -3 -c "import requests,Crypto,fastapi" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto runpy

echo No Python with dependencies found.
echo Run:  pip install -r requirements.txt
echo then double-click this file again.
pause
exit /b 1

:runpy
%PY% "%~dp0panel\app.py"
echo.
echo Panel stopped. Close this window.
pause >nul
exit /b 0
