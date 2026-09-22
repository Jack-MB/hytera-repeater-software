# -*- coding: utf-8 -*-
"""
usv_diagnose.py  â€“  VFI 2000 ICR IoT Modbus TCP Diagnose

Zeigt alle rohen Register-Werte und interpretiert sie.
NÃ¼tzlich um die Register-Map zu verifi zieren.

Starten: python usv_diagnose.py
"""
import sys, time

USV_IP   = "192.168.1.102"   # aus config.json
MB_PORT  = 502

def fmt_reg(val, div=1, unit=""):
    if val is None: return "â€”"
    if div != 1:
        return f"{val/div:.1f} {unit}".strip()
    return f"{val} {unit}".strip()

print(f"""
======================================================================
  BlueWalker VFI 2000 ICR IoT  â€“  Modbus TCP Diagnose
  IP: {USV_IP}  Port: {MB_PORT}
======================================================================
""")

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    print("  [FEHLER] pymodbus nicht installiert!")
    print("  LÃ¶sung:  pip install pymodbus")
    sys.exit(1)

print(f"  Verbinde zu {USV_IP}:{MB_PORT} ...")
client = ModbusTcpClient(host=USV_IP, port=MB_PORT, timeout=5)

if not client.connect():
    print(f"\n  [FEHLER] Verbindung fehlgeschlagen!")
    print("  PrÃ¼fe:")
    print("  1. USV an und Ethernet-Kabel verbunden?")
    print("  2. IP korrekt? (aktuell: " + USV_IP + ")")
    print("  3. Modbus TCP in USV aktiviert? (LCD â†’ Settings â†’ Communication â†’ Modbus TCP â†’ ON)")
    sys.exit(1)

print("  [OK] Verbunden!\n")

# Rohe Register lesen (FC=04, Input Registers)
print("  Lese Input Register 0..19 (FC=04)...")
res = client.read_input_registers(address=0, count=20, device_id=1)

if res.isError():
    print(f"  [FEHLER] Modbus-Fehler: {res}")
    # Versuche Holding Registers
    print("  Versuche Holding Register (FC=03)...")
    res2 = client.read_holding_registers(address=0, count=20, device_id=1)
    if res2.isError():
        print(f"  [FEHLER] Auch Holding Register nicht lesbar: {res2}")
        client.close()
        sys.exit(1)
    else:
        regs = res2.registers
        print("  [OK] Holding Register lesbar!\n")
        print("  HINWEIS: usv_monitor.py muss auf FC=03 umgestellt werden.\n")
else:
    regs = res.registers

client.close()

print(f"\n  {'Reg':>4}  {'Rohwert':>8}  Interpretation")
print("  " + "-" * 52)

KNOWN = {
    0:  ("Eingang Spannung",  "V",  10),
    1:  ("Eingang Frequenz",  "Hz", 10),
    2:  ("Ausgang Spannung",  "V",  10),
    3:  ("Ausgang Frequenz",  "Hz", 10),
    4:  ("Last",              "%",   1),
    5:  ("Batterie Spannung", "V",  100),
    6:  ("Batterie Ladung",   "%",   1),
    7:  ("Restlaufzeit",      "min", 1),
    8:  ("Netz-Status",       "",    1),
    9:  ("USV-Status",        "",    1),
   10:  ("Temperatur",        "Â°C",  1),
   11:  ("Alarme",            "",    1),
}

for i, val in enumerate(regs):
    if i in KNOWN:
        name, unit, div = KNOWN[i]
        if div > 1:
            interp = f"{val/div:.2f} {unit}"
        elif unit:
            interp = f"{val} {unit}"
        else:
            interp = f"{val} (0=Normal, 1=Alarm/Batterie)"
        print(f"  {i:>4}  {val:>8}  {name}: {interp}")
    else:
        print(f"  {i:>4}  {val:>8}  (unbekannt)")

print("\n----------------------------------------------------------------------")

# Jetzt die interpretierte Ansicht
try:
    from usv_monitor import USVMonitor
    mon = USVMonitor(USV_IP)
    d   = mon.poll()
    if d.erreichbar:
        print(f"\n  USV-Daten (interpretiert):\n")
        print(f"  Batterie:      {d.batterie_pct}%  ({d.batterie_status})")
        print(f"  Batt-Spannung: {d.batt_volt} V")
        print(f"  Restlaufzeit:  {d.batterie_min} min")
        print(f"  Netz:          {d.netz_status}")
        print(f"  Eingang:       {d.eingang_volt} V  /  {d.eingang_hz} Hz")
        print(f"  Ausgang:       {d.ausgang_volt} V  /  {d.ausgang_hz} Hz")
        print(f"  Last:          {d.last_pct} %")
        print(f"  Temperatur:    {d.temperatur} Â°C")
        print(f"  Alarme:        {d.alarme}")
    else:
        print(f"\n  [FEHLER] {d.fehler}")
except Exception as e:
    print(f"\n  Monitor-Fehler: {e}")

print("\n======================================================================")

