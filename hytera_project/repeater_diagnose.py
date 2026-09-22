# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
repeater_diagnose.py – Hytera HR1065 Vollständige SNMP-Diagnose
================================================================
Liest via SNMP alle verfügbaren Repeater-Daten aus:
  - VSWR (Stehwellenverhältnis)
  - Vorwärts- und Rückwärtsleistung (Watt)
  - PA-Temperatur
  - Versorgungsspannung
  - Alarm-Status (VSWR, Temperatur, PLL, Lüfter, Spannung)
  - Fehlerprotokoll (bis zu 100 Einträge)
  - Systeminfo (Modell, Firmware, Seriennummer, DMR-ID)
  - Aktuelle Frequenzen (TX/RX), Kanalname
  - Arbeitsstatus (Senden/Empfangen)
  - RSSI Slot 1 und Slot 2

Voraussetzung:
  pip install pysnmp

Verwendung:
  python repeater_diagnose.py [IP] [Community]
  Standardwerte: IP=192.168.1.100, Community=public
"""

import asyncio
import struct
import sys
import json
import datetime
from pathlib import Path

try:
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity,
        get_cmd, walk_cmd
    )
except ImportError:
    print("FEHLER: pysnmp nicht installiert.")
    print("  pip install pysnmp")
    sys.exit(1)

# ── Hytera Enterprise OID Basis ─────────────────────────────────────────────
BASE = "1.3.6.1.4.1.40297.1.2"

# ── Alarm-OIDs (rptAlarmInfo) ────────────────────────────────────────────────
ALARMS = {
    f"{BASE}.1.1.1": ("Spannungs-Alarm",      {0:"Normal", 1:"Unterspannung", 2:"Überspannung"}),
    f"{BASE}.1.1.2": ("Temperatur-Alarm",     {0:"Normal", 1:"Zu kalt", 2:"Zu heiß"}),
    f"{BASE}.1.1.3": ("Lüfter-Alarm",         {0:"Normal", 1:"ALARM"}),
    f"{BASE}.1.1.4": ("Vorwärtsleistung-Alarm",{-1:"n/v",   0:"Normal", 1:"ALARM"}),
    f"{BASE}.1.1.5": ("Rückwärtsleistung-Alarm",{-1:"n/v",  0:"Normal", 1:"ALARM"}),
    f"{BASE}.1.1.6": ("VSWR-Alarm",           {0:"Normal", 1:"ALARM ⚠"}),
    f"{BASE}.1.1.7": ("TX-PLL-Alarm",         {0:"Normal", 1:"ALARM"}),
    f"{BASE}.1.1.8": ("RX-PLL-Alarm",         {0:"Normal", 1:"ALARM"}),
    f"{BASE}.1.1.9": ("Batterie-Alarm",        {0:"Normal", 1:"Fehler"}),
}

# ── Echtzeit-Daten (rptDataInfo) ─────────────────────────────────────────────
DATA_FLOAT = {
    f"{BASE}.1.2.1": "Versorgungsspannung (V)",
    f"{BASE}.1.2.2": "PA-Temperatur (°C)",
    f"{BASE}.1.2.4": "VSWR",
    f"{BASE}.1.2.5": "Vorwärtsleistung (W)",
    f"{BASE}.1.2.6": "Rückwärtsleistung (W)",
}
DATA_INT = {
    f"{BASE}.1.2.9":  "RSSI Slot 1 (dB)",
    f"{BASE}.1.2.10": "RSSI Slot 2 (dB)",
    f"{BASE}.1.2.11": ("Versorgungstyp", {0:"DC", 1:"Batterie"}),
    f"{BASE}.1.2.12": ("Batterie verbunden", {0:"Nein", 1:"Ja"}),
}

# ── Systeminfo (rptSystemInfo) ────────────────────────────────────────────────
SYSINFO = {
    f"{BASE}.4.1":  "Modellname",
    f"{BASE}.4.2":  "Modellnummer",
    f"{BASE}.4.3":  "Firmware-Version",
    f"{BASE}.4.4":  "RCDB-Version",
    f"{BASE}.4.5":  "Seriennummer",
    f"{BASE}.4.6":  "Repeater-Name",
    f"{BASE}.4.7":  "DMR-Repeater-ID",
    f"{BASE}.4.8":  ("Kanaltyp", {0:"Digital", 1:"Analog", 2:"Gemischt"}),
    f"{BASE}.4.9":  "Kanalname",
    f"{BASE}.4.10": "TX-Frequenz (Hz)",
    f"{BASE}.4.11": "RX-Frequenz (Hz)",
    f"{BASE}.4.12": ("Arbeitsstatus", {0:"Empfangen", 1:"Senden"}),
    f"{BASE}.4.13": "Zonenname",
}

# ── Steuerung/Status (rptControl) ────────────────────────────────────────────
CONTROL = {
    f"{BASE}.2.3":  ("Kanaltyp", {0:"Digital", 1:"Analog", 2:"Gemischt"}),
    f"{BASE}.2.5":  ("TX-Leistungsstufe", {0:"Hoch", 2:"Niedrig"}),
    f"{BASE}.2.6":  ("Repeating", {0:"Aktiv", 1:"Unterdrückt"}),
    f"{BASE}.2.7":  ("Radio-Status", {0:"Aktiv", 1:"Deaktiviert"}),
}

# ── Alarm-Log Namen ────────────────────────────────────────────────────────────
LOG_ALARM_NAMES = {
    0: "Temperatur",
    1: "Lüfter",
    2: "VSWR",
    3: "Niedrige Vorwärtsleistung",
    4: "Unterspannung",
    5: "Überspannung",
    6: "TX-PLL Unlock",
    7: "RX-PLL Unlock",
    8: "Batteriespannung",
    65535: "(leer)",
}


def decode_float(raw) -> str:
    """Konvertiert 4-Byte OCTET STRING → IEEE 754 float."""
    try:
        b = bytes(raw)
        if len(b) == 4:
            val = struct.unpack(">f", b)[0]
            return f"{val:.3f}"
    except Exception:
        pass
    return str(raw)


def decode_unicode(raw) -> str:
    """Konvertiert UTF-16-LE OCTET STRING → lesbarer String."""
    try:
        b = bytes(raw)
        # Hytera nutzt UTF-16-LE, null-terminiert
        text = b.decode("utf-16-le", errors="ignore").rstrip("\x00").strip()
        if text:
            return text
    except Exception:
        pass
    try:
        return raw.prettyPrint().strip()
    except Exception:
        return str(raw)


async def snmp_get(engine, transport, community, oid_str):
    """Einzelnen OID abfragen."""
    try:
        err_ind, err_stat, _, var_binds = await get_cmd(
            engine,
            CommunityData(community, mpModel=0),
            transport,
            ContextData(),
            ObjectType(ObjectIdentity(oid_str)),
        )
        if err_ind or err_stat:
            return None
        if var_binds:
            return var_binds[0][1]
    except Exception:
        return None
    return None


async def snmp_walk(engine, transport, community, base_oid):
    """SNMP-Walk ab base_oid."""
    results = []
    try:
        iterator = walk_cmd(
            engine,
            CommunityData(community, mpModel=0),
            transport,
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False,
        )
        async for err_ind, err_stat, _, var_binds in iterator:
            if err_ind or err_stat:
                break
            for oid, val in var_binds:
                results.append((str(oid), val))
    except Exception:
        pass
    return results


def print_section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def print_row(label: str, value: str, alarm: bool = False):
    prefix = "[!] " if alarm else "    "
    print(f"{prefix}{label:<35} {value}")


async def run_diagnose(ip: str, community: str):
    print(f"\n{'='*60}")
    print(f"  HYTERA HR1065 - Vollstaendige Repeater-Diagnose")
    print(f"  IP: {ip}   Community: {community}")
    print(f"  Zeit: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"{'='*60}")

    engine = SnmpEngine()
    try:
        transport = await UdpTransportTarget.create((ip, 161), timeout=3, retries=2)
    except Exception as e:
        print(f"\nFEHLER: Verbindung zu {ip}:161 fehlgeschlagen – {e}")
        return

    results = {}

    # ── 1. SYSTEM-INFO ──────────────────────────────────────────────────────
    print_section("📋 SYSTEM-INFORMATION")
    for oid, label in SYSINFO.items():
        val = await snmp_get(engine, transport, community, oid)
        if val is None:
            continue
        if isinstance(label, tuple):
            name, mapping = label
            raw_int = int(val)
            text = mapping.get(raw_int, str(raw_int))
            print_row(name, text)
            results[name] = text
        else:
            # Frequenz in MHz anzeigen
            if "Frequenz" in label:
                try:
                    hz = int(val)
                    mhz = hz / 1_000_000
                    display = f"{mhz:.5f} MHz  ({hz} Hz)"
                except Exception:
                    display = str(val)
            elif label in ("Modellname", "Modellnummer", "Firmware-Version",
                           "RCDB-Version", "Seriennummer", "Repeater-Name",
                           "Kanalname", "Zonenname"):
                display = decode_unicode(val)
            else:
                display = val.prettyPrint() if hasattr(val, "prettyPrint") else str(val)
            print_row(label, display)
            results[label] = display

    # ── 2. ALARM-STATUS ─────────────────────────────────────────────────────
    print_section("🚨 ALARM-STATUS")
    any_alarm = False
    for oid, (name, mapping) in ALARMS.items():
        val = await snmp_get(engine, transport, community, oid)
        if val is None:
            continue
        raw = int(val)
        text = mapping.get(raw, str(raw))
        is_alarm = raw not in (0, -1)
        if is_alarm:
            any_alarm = True
        print_row(name, text, alarm=is_alarm)
        results[f"Alarm_{name}"] = text

    if not any_alarm:
        print("   ✅ Alle Alarme: Normal")

    # ── 3. ECHTZEIT-MESSWERTE ───────────────────────────────────────────────
    print_section("📊 ECHTZEIT-MESSWERTE")
    for oid, label in DATA_FLOAT.items():
        val = await snmp_get(engine, transport, community, oid)
        if val is None:
            continue
        text = decode_float(val)
        is_alarm = False
        if "VSWR" in label:
            try:
                f = float(text)
                # VSWR > 2.0 gilt als kritisch
                is_alarm = f > 2.0
                quality = "✅ Gut" if f < 1.5 else ("⚠ Erhöht" if f < 2.0 else "🔴 Kritisch")
                text = f"{text}  → {quality}"
            except ValueError:
                pass
        print_row(label, text, alarm=is_alarm)
        results[label] = text

    for oid, label in DATA_INT.items():
        val = await snmp_get(engine, transport, community, oid)
        if val is None:
            continue
        if isinstance(label, tuple):
            name, mapping = label
            text = mapping.get(int(val), str(val))
            print_row(name, text)
        else:
            raw = int(val)
            if "RSSI" in label and raw == -200:
                text = "n/v (kein Signal)"
            else:
                text = str(raw)
            print_row(label, text)

    # ── 4. STEUERUNG / BETRIEBSSTATUS ───────────────────────────────────────
    print_section("⚙️  BETRIEBSSTATUS")
    for oid, label in CONTROL.items():
        val = await snmp_get(engine, transport, community, oid)
        if val is None:
            continue
        if isinstance(label, tuple):
            name, mapping = label
            text = mapping.get(int(val), str(val))
            print_row(name, text)

    # ── 5. ALARM-FEHLERPROTOKOLL ─────────────────────────────────────────────
    print_section("📜 ALARM-FEHLERPROTOKOLL (letzte 100 Einträge)")

    # Anzahl Einträge
    count_val = await snmp_get(engine, transport, community, f"{BASE}.3.3")
    latest_val = await snmp_get(engine, transport, community, f"{BASE}.3.4")
    count = int(count_val) if count_val is not None else 0
    latest = int(latest_val) if latest_val is not None else 0

    print_row("Einträge gesamt", str(count))
    print_row("Neuester Eintrag (Zeile)", str(latest))

    if count > 0:
        print(f"\n   {'Nr':>3}  {'Alarm':<30}  {'Status':<15}  {'Zeit (Sekunden seit Boot)'}")
        print(f"   {'-'*3}  {'-'*30}  {'-'*15}  {'-'*25}")
        for i in range(1, min(count + 1, 101)):
            alarm_name_val = await snmp_get(engine, transport, community, f"{BASE}.3.1.1.{i}.2")
            alarm_stat_val = await snmp_get(engine, transport, community, f"{BASE}.3.1.1.{i}.3")
            log_time_val   = await snmp_get(engine, transport, community, f"{BASE}.3.1.1.{i}.4")
            if alarm_name_val is None:
                continue
            alarm_n = int(alarm_name_val)
            alarm_s = int(alarm_stat_val) if alarm_stat_val is not None else 65535
            log_t   = int(log_time_val)   if log_time_val is not None else -1
            if alarm_n == 65535 or log_t == -1:
                continue
            alarm_txt = LOG_ALARM_NAMES.get(alarm_n, f"Alarm #{alarm_n}")
            status_txt = "✅ Behoben" if alarm_s == 0 else "🔴 Aktiv"
            time_txt = f"{log_t}s" if log_t >= 0 else "n/v"
            print(f"   {i:>3}  {alarm_txt:<30}  {status_txt:<15}  {time_txt}")
    else:
        print("   ✅ Keine Alarm-Einträge im Protokoll")

    # ── 6. ZUSAMMENFASSUNG ───────────────────────────────────────────────────
    print_section("✅ DIAGNOSE ABGESCHLOSSEN")
    print(f"   Repeater {ip} erfolgreich abgefragt.")
    print(f"   Timestamp: {datetime.datetime.now().isoformat()}")

    # Ergebnis als JSON speichern
    out_file = Path(__file__).parent / "repeater_diag_result.json"
    results["timestamp"] = datetime.datetime.now().isoformat()
    results["repeater_ip"] = ip
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"   JSON gespeichert: {out_file.name}")
    print()


def main():
    # IP und Community aus config.json lesen
    config_path = Path(__file__).parent / "config.json"
    ip = "192.168.1.100"
    community = "public"

    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            ip = cfg.get("repeater_ip", ip)
        except Exception:
            pass

    # Kommandozeilenargumente haben Vorrang
    if len(sys.argv) >= 2:
        ip = sys.argv[1]
    if len(sys.argv) >= 3:
        community = sys.argv[2]

    asyncio.run(run_diagnose(ip, community))


if __name__ == "__main__":
    main()
