@echo off
set WORKDIR=%~dp0..
cd /d "%WORKDIR%"

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment tidak ditemukan. Jalankan setup.py dulu.
  pause
  exit /b 1
)

echo Menjalankan bot...
"%WORKDIR%\.venv\Scripts\python.exe" "%WORKDIR%\scripts\claude_telegram.py"
pause
