@echo off
echo ============================================
echo   SynthFlow - Build Script for Windows
echo ============================================
echo.

REM Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://python.org
    pause
    exit /b 1
)

REM Install dependencies
echo [1/3] Installing dependencies...
pip install openai sounddevice soundfile numpy pyperclip keyboard pystray pillow pynput pyinstaller --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

REM Build executable
echo [2/3] Building SynthFlow.exe...
pyinstaller synthflow.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: Build failed
    pause
    exit /b 1
)

echo [3/3] Done!
echo.
echo SynthFlow.exe is in the  dist\  folder.
echo Double-click it to run - it will appear in your system tray.
echo.
pause
