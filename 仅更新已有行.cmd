@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 此模式只更新共同 ID：新行不加载，缺少的旧行保留。它不是完整重载。
if exist SekiroParamHotload.exe (
  SekiroParamHotload.exe --existing-only %*
) else (
  python hotload.py --existing-only %*
)
pause
