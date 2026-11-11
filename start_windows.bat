@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m edge_batch gui
) else (
  python -m edge_batch gui
  if errorlevel 1 pause
)
