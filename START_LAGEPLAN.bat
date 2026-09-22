@echo off
cd /d "%~dp0"
echo ======================================================
echo  Hytera HR1065 // Taktischer Lageplan ^& Timeline
echo ======================================================
echo.
python run_map_app.py %*
pause
