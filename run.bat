@echo off
title GeoQC Pro
echo.
echo  ================================================
echo    GeoQC Pro - Module 2
echo  ================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERREUR: Python non trouve.
    echo  Telecharge Python 3.10+ sur https://python.org
    pause
    exit /b 1
)

:: Install dependencies if needed
echo  Verification des dependances...
pip install -r requirements.txt --quiet --break-system-packages 2>nul
pip install -r requirements.txt --quiet 2>nul

:: Create folders
if not exist "uploads" mkdir uploads
if not exist "output" mkdir output
if not exist "historique" mkdir historique

:: Launch
echo.
echo  Demarrage du serveur...
echo  Ouvre http://localhost:5000 dans ton navigateur
echo.
start "" http://localhost:5000
python app.py

pause
