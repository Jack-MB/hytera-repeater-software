@echo off
:: Pruefen ob Admin-Rechte vorhanden
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Erfordert Administrator-Rechte - UAC-Dialog erscheint...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo   Hytera NAI - Firewall-Regeln setzen
echo ============================================
echo.

netsh advfirewall firewall delete rule name="Hytera CC Slt1 UDP 30009" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera CC Slt2 UDP 30010" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera RRS Slt1 UDP 30001" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera RRS Slt2 UDP 30002" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera Audio Slt1 UDP 30012" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera Audio Slt2 UDP 30014" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera GPS UDP 30003" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera SMS UDP 30007" >nul 2>&1
netsh advfirewall firewall delete rule name="Hytera SNMP Trap UDP 10162" >nul 2>&1

netsh advfirewall firewall add rule name="Hytera CC Slt1 UDP 30009"    dir=in action=allow protocol=UDP localport=30009
netsh advfirewall firewall add rule name="Hytera CC Slt2 UDP 30010"    dir=in action=allow protocol=UDP localport=30010
netsh advfirewall firewall add rule name="Hytera RRS Slt1 UDP 30001"   dir=in action=allow protocol=UDP localport=30001
netsh advfirewall firewall add rule name="Hytera RRS Slt2 UDP 30002"   dir=in action=allow protocol=UDP localport=30002
netsh advfirewall firewall add rule name="Hytera Audio Slt1 UDP 30012" dir=in action=allow protocol=UDP localport=30012
netsh advfirewall firewall add rule name="Hytera Audio Slt2 UDP 30014" dir=in action=allow protocol=UDP localport=30014
netsh advfirewall firewall add rule name="Hytera GPS UDP 30003"        dir=in action=allow protocol=UDP localport=30003
netsh advfirewall firewall add rule name="Hytera SMS UDP 30007"        dir=in action=allow protocol=UDP localport=30007
netsh advfirewall firewall add rule name="Hytera SNMP Trap UDP 10162"  dir=in action=allow protocol=UDP localport=10162

echo.
echo Alle Regeln gesetzt!
echo.
pause
