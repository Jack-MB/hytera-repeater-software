@echo off
title Hytera Dispatcher
color 0A
echo ==========================================
echo  Hytera HR1065 Dispatcher - Startet...
echo ==========================================
echo.

REM Python-Version pruefen
python --version 2>NUL
if errorlevel 1 (
    echo [FEHLER] Python nicht gefunden!
    echo Bitte Python 3.10+ installieren: https://python.org
    pause
    exit /b 1
)

echo [OK] Python gefunden
echo [INFO] Starte Dispatcher...
echo.
echo Strg+C zum Beenden (oder App-Fenster schliessen)
echo ------------------------------------------
echo.

REM App mit unverzuegerter Ausgabe starten
python -u app.py

echo.
echo [INFO] Dispatcher beendet.
pause
