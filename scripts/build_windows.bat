@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo === AncelLauncher build ===
echo.

python --version
if errorlevel 1 (
  echo ERROR: Python not found. Install Python 3.11 or 3.12 from python.org
  pause
  exit /b 1
)

python -m pip install -U pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: pip failed
  pause
  exit /b 1
)

echo Cleaning old files...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist AncelLauncher.exe del /f /q AncelLauncher.exe

echo Building... please wait
python -m PyInstaller AncelLauncher.spec --noconfirm --clean
if errorlevel 1 (
  echo ERROR: PyInstaller failed
  if exist build rmdir /s /q build
  pause
  exit /b 1
)

if not exist dist\AncelLauncher.exe (
  echo ERROR: dist\AncelLauncher.exe not found
  if exist build rmdir /s /q build
  pause
  exit /b 1
)

echo Copying exe to project root...
copy /y dist\AncelLauncher.exe AncelLauncher.exe >nul

echo Removing temporary folders...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo ============================================
echo SUCCESS
echo.
echo Run:
echo   %CD%\AncelLauncher.exe
echo ============================================
echo.
pause
