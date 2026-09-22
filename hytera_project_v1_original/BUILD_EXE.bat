@echo off
title Hytera - EXE Erstellen
echo ==========================================
echo  Hytera Dispatcher - EXE Builder
echo ==========================================

echo [1/3] Installiere PyInstaller...
pip install pyinstaller --quiet

echo [2/3] Erstelle EXE (kann 2-3 Minuten dauern)...
pyinstaller ^
  --onedir ^
  --windowed ^
  --name "HyteraDispatcher" ^
  --add-data "timeline_viewer.html;." ^
  --add-data "ids.json;." ^
  --add-data "config.json;." ^
  --hidden-import "tkinter" ^
  --hidden-import "queue" ^
  --hidden-import "threading" ^
  --clean ^
  app.py

echo.
echo [3/3] Fertig!
echo EXE liegt in: dist\HyteraDispatcher\HyteraDispatcher.exe
echo.
echo WICHTIG: Den gesamten Ordner 'dist\HyteraDispatcher' weitergeben
echo          (nicht nur die EXE allein)
pause
