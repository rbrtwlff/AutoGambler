@echo off
setlocal
cd /d "%~dp0"

echo.
echo weltanschauung-sim Dashboard
echo ============================
echo.
echo Das Dashboard wird lokal gestartet.
echo Streamlit oeffnet normalerweise automatisch ein Browserfenster.
echo Falls kein Browser aufgeht, schauen Sie bitte auf die Adresse in diesem Fenster.
echo.

set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"
if not exist "%PYTHON_EXE%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo FEHLER: Python wurde nicht gefunden.
    echo Bitte zuerst install.bat ausfuehren oder Python 3.12+ installieren.
    echo.
    pause
    exit /b 1
  )
  set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" -m streamlit run src/wsim/dashboard/app.py
if errorlevel 1 (
  echo.
  echo FEHLER: Das Dashboard konnte nicht gestartet werden.
  echo Bitte pruefen Sie die Meldungen oben.
  echo Wenn Streamlit fehlt, fuehren Sie install.bat erneut aus.
  echo.
  pause
  exit /b 1
)
