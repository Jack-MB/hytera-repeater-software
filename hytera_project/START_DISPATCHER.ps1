# START_DISPATCHER.ps1
# Startet den Hytera Dispatcher automatisch als Administrator

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appPy     = Join-Path $scriptDir "app.py"

# Pruefen ob bereits Admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    # Neu starten als Administrator
    Write-Host "[INFO] Starte mit Administrator-Rechten (UAC-Abfrage erscheint)..."
    Start-Process powershell -Verb RunAs -ArgumentList (
        "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    )
    exit
}

# Als Admin: Python starten
Set-Location $scriptDir
Write-Host "=========================================="
Write-Host " Hytera HR1065 Dispatcher - Administrator"
Write-Host "=========================================="
Write-Host ""
Write-Host "[INFO] Verzeichnis: $scriptDir"
Write-Host ""

& python -u $appPy

Write-Host ""
Write-Host "[INFO] Dispatcher beendet."
Read-Host "Druecken Sie Enter zum Schliessen"
