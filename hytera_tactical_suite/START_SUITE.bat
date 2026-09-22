@echo off
title Hytera Tactical Suite - Lagezentrum und Funkfuehrung
cd /d "%~dp0"
echo [1/2] Pruefe Python-Umgebung...
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python wurde nicht im Systempfad gefunden!
    echo Bitte installieren Sie Python 3.11+ und aktivieren Sie "Add to PATH".
    pause
    exit /b 1
)
echo [2/2] Starte Hytera Tactical Suite...
python run_suite.py %*
if errorlevel 1 (
    echo.
    echo [FEHLER] Server wurde mit einem Fehler beendet.
    pause
)
