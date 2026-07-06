@echo off
setlocal
cd /d "%~dp0"

echo.
echo weltanschauung-sim Installation
echo ==============================
echo.
echo Dieses Fenster richtet das lokale Simulationsprogramm ein.
echo Bitte schliessen Sie es erst, wenn am Ende eine Erfolgsmeldung erscheint.
echo.

set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"
if not exist "%PYTHON_EXE%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo FEHLER: Python wurde nicht gefunden.
    echo.
    echo Bitte installieren Sie Python 3.12 oder neuer von:
    echo https://www.python.org/downloads/
    echo.
    echo Wichtig: Beim Installieren die Option "Add Python to PATH" aktivieren.
    echo Danach dieses Fenster schliessen und install.bat erneut doppelklicken.
    echo.
    pause
    exit /b 1
  )
  set "PYTHON_EXE=python"
)

echo Verwende Python:
"%PYTHON_EXE%" --version
if errorlevel 1 (
  echo.
  echo FEHLER: Python konnte nicht gestartet werden.
  pause
  exit /b 1
)

echo.
echo Installiere Abhaengigkeiten. Das kann einige Minuten dauern...
"%PYTHON_EXE%" -m pip install -e ".[dev]"
if errorlevel 1 (
  echo.
  echo FEHLER: Die Installation ist fehlgeschlagen.
  echo Bitte pruefen Sie die Meldungen oben.
  pause
  exit /b 1
)

echo.
echo Fuehre einen kurzen Funktionstest aus...
"%PYTHON_EXE%" -m pytest tests/test_smoke.py
if errorlevel 1 (
  echo.
  echo FEHLER: Der Smoke Test ist fehlgeschlagen.
  echo Bitte pruefen Sie die Meldungen oben.
  pause
  exit /b 1
)

echo.
echo Installation erfolgreich.
echo Sie koennen jetzt start_dashboard.bat doppelklicken.
echo.
pause
