@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" -m streamlit run src\autogambler\dashboard\app.py
) else (
  python -m streamlit run src\autogambler\dashboard\app.py
)
