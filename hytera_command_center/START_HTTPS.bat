@echo off
chcp 65001 >nul
title Hytera Command Center (HTTPS Modus)

echo ========================================================
echo   Hytera Command Center – Start im sicheren HTTPS-Modus
echo ========================================================
echo.
echo Starte Webserver mit SSL-Verschluesselung auf Port 8443...
echo.

cd /d "%~dp0"
python run.py --ssl

if errorlevel 1 (
    echo.
    echo [FEHLER] Server konnte nicht gestartet werden.
    pause
)
