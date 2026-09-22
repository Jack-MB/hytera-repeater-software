@echo off
title Hytera Command Center
color 0A
chcp 65001 >nul

echo.
echo  +==========================================+
echo  ^|   Hytera Command Center - ELW System    ^|
echo  ^|   Version 1.0.0                         ^|
echo  +==========================================+
echo.

cd /d "%~dp0"

:: Python pruefen
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python nicht gefunden!
    echo Bitte Python 3.10+ installieren: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Laufende Instanz auf Port 8000 beenden (verhindert "Port belegt"-Fehler)
echo [INFO] Pruefe Port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: Starten
echo [INFO] Starte Server...
echo [INFO] Browser: http://localhost:8000
echo.
echo Zum Beenden: STRG+C
echo.

python run.py

pause