@echo off
set "SCRIPT_DIR=%~dp0"

if defined PDF2ZH_GUI_PYTHONW (
  set "PYTHONW=%PDF2ZH_GUI_PYTHONW%"
) else if exist "%USERPROFILE%\.conda\envs\agent_work_env\pythonw.exe" (
  set "PYTHONW=%USERPROFILE%\.conda\envs\agent_work_env\pythonw.exe"
) else (
  set "PYTHONW=pythonw"
)

start "" "%PYTHONW%" "%SCRIPT_DIR%pdf2zh_gui.py"
