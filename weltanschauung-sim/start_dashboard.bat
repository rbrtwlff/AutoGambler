@echo off
setlocal
cd /d "%~dp0"
python -m streamlit run src/wsim/dashboard/app.py
