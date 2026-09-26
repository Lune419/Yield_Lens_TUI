@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.10 or newer from python.org first.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo Failed to create Python environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import numpy, xlrd, openpyxl" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed. Check your internet connection.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" yield_tui.py %*
pause
