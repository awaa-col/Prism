@echo off
setlocal
set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..\
cd /d %ROOT_DIR%
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe scripts\prism_cli.py %*
) else (
  python scripts\prism_cli.py %*
)
endlocal

