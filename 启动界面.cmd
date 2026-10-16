@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist SekiroParamHotloadGUI.exe (
  start "" "%~dp0SekiroParamHotloadGUI.exe"
) else (
  start "" pythonw "%~dp0gui.py"
)
