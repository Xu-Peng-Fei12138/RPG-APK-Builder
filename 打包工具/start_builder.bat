@echo off
chcp 65001 >nul
title RPGMMV APK Builder

echo ================================================
echo   RPGMMV -> Android APK Build Tool
echo ================================================
echo.

py -3.14 -c "import tkinter" 2>nul
if %errorlevel% == 0 (
    echo [OK] Starting with Python 3.14...
    py -3.14 "%~dp0builder_gui.py"
    goto done
)

python -c "import tkinter" 2>nul
if %errorlevel% == 0 (
    echo [OK] Starting with system Python...
    python "%~dp0builder_gui.py"
    goto done
)

echo [ERROR] Python with tkinter not found.
echo.
echo Please install standard Python and check "tcl/tk and IDLE" during setup.
echo Or manually run: py -3.14 builder_gui.py
echo.
pause

:done
pause
