@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if defined PDF2ZH_GUI_PYTHONW (
  set "PYTHONW=%PDF2ZH_GUI_PYTHONW%"
) else if exist "%SCRIPT_DIR%.venv\Scripts\pythonw.exe" (
  set "PYTHONW=%SCRIPT_DIR%.venv\Scripts\pythonw.exe"
) else (
  set "PYTHONW=pythonw"
)

start "" "%PYTHONW%" "%SCRIPT_DIR%pdf2zh_gui.py"
endlocal
