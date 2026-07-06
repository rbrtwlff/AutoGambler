@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" -m pip install -e ".[dev]"
) else (
  python -m pip install -e ".[dev]"
)
pause

