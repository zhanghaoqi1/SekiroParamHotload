@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist SekiroParamHotload.exe (
  SekiroParamHotload.exe --full-reload --dry-run --once %*
) else (
  python hotload.py --full-reload --dry-run --once %*
)
pause
