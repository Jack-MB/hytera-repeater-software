# -*- coding: utf-8 -*-
"""
repeater_trap_monitor.py
========================
Empfaengt SNMP-Traps vom Hytera HR1065 Repeater.
Dekodiert ALLE bekannten Hytera-MIB-OIDs (Alarm + Performance + BaseInfo).

CPS-Einstellung:
  Common -> Setting -> SNMP Trap Port: 10162  (kein Admin noetig)
  SNMP Trap IP: <PC-IP>
"""

import socket
import struct
import threading
import time
import json
import os
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any

# # ── Vollstaendiges OID-Mapping – HYTERA-REPEATER-MIB (LibreNMS verifiziert) ──
# Quelle: github.com/librenms/librenms/blob/master/mibs/hytera/HYTERA-REPEATER-MIB

# Gruppe 1: Alarm-Flags rptAlarmInfo (1.3.6.1.4.1.40297.1.2.1.1.x)
# 0=normal, 1=alarm  (Spannung/Temp koennen auch 2=high alarm)
ALARM_FLAG_OIDS = {
    "1.3.6.1.4.1.40297.1.2.1.1.1": ("spannung",    "Spannung",           "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.2": ("temperatur",  "Temperatur",         "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.3": ("luefter",     "Luefter",            "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.4": ("fw_leistung", "Fwd-Power-Alarm",    "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.5": ("rw_leistung", "Rfl-Power-Alarm",    "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.6": ("vswr_alarm",  "VSWR-Alarm",         "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.7": ("tx_pll",      "TX-PLL-Alarm",       "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.8": ("rx_pll",      "RX-PLL-Alarm",       "alarm"),
    "1.3.6.1.4.1.40297.1.2.1.1.9": ("batterie",    "Batterie-Alarm",     "alarm"),
}

# Gruppe 2: Messwerte rptDataInfo (1.3.6.1.4.1.40297.1.2.1.2.x)
# OCTET STRING(SIZE 4) = IEEE 754 Big-Endian float
PERF_OIDS = {
    "1.3.6.1.4.1.40297.1.2.1.2.1":  ("volt_wert",   "Spannung V",      "float"),
    "1.3.6.1.4.1.40297.1.2.1.2.2":  ("temp_wert",   "Temperatur C",    "float"),
    "1.3.6.1.4.1.40297.1.2.1.2.3":  ("fan_wert",    "Luefter RPM",     "int"),
    "1.3.6.1.4.1.40297.1.2.1.2.4":  ("vswr_wert",   "VSWR",            "float"),
    "1.3.6.1.4.1.40297.1.2.1.2.5":  ("fw_pwr_watt", "Fwd-Power W",     "float"),
    "1.3.6.1.4.1.40297.1.2.1.2.6":  ("rw_pwr_watt", "Rfl-Power W",     "float"),
    # .7 .8 = Backup-Objekte (nicht implementiert laut MIB)
    "1.3.6.1.4.1.40297.1.2.1.2.9":  ("rssi_slot1",  "RSSI TS1 dB",     "int"),
    "1.3.6.1.4.1.40297.1.2.1.2.10": ("rssi_slot2",  "RSSI TS2 dB",     "int"),
    "1.3.6.1.4.1.40297.1.2.1.2.11": ("power_type",  "Stromquelle",     "int"),   # 0=DC, 1=Bat
    "1.3.6.1.4.1.40297.1.2.1.2.12": ("batt_connect","Batterie verb.",   "int"),   # 0=nein, 1=ja
    "1.3.6.1.4.1.40297.1.2.1.2.13": ("batt_volt",   "Batterie V",      "float"),
}

# Gruppe 3: rptSystemInfo (1.3.6.1.4.1.40297.1.2.4.x) – Geraetestatus
# Diese OIDs werden in den "Local Machine Info Traps" gesendet
SYSINFO_OIDS = {
    "1.3.6.1.4.1.40297.1.2.4.1":  ("modell_name", "Modellname",     "str_unicode"),
    "1.3.6.1.4.1.40297.1.2.4.2":  ("modell_nr",   "Modellnummer",   "str_unicode"),
    "1.3.6.1.4.1.40297.1.2.4.3":  ("fw_version",  "Firmware",       "str_unicode"),
    "1.3.6.1.4.1.40297.1.2.4.4":  ("rcdb_version","RCDB-Version",   "str_unicode"),
    "1.3.6.1.4.1.40297.1.2.4.5":  ("seriennr",    "Seriennummer",   "str_unicode"),
    "1.3.6.1.4.1.40297.1.2.4.6":  ("rpt_alias",   "Repeater-Alias", "str_unicode"),
    "1.3.6.1.4.1.40297.1.2.4.7":  ("radio_id",    "Radio-ID",       "int"),
    "1.3.6.1.4.1.40297.1.2.4.8":  ("ch_type",     "Kanaltyp",       "int"),       # 0=dig,1=ana
    "1.3.6.1.4.1.40297.1.2.4.9":  ("ch_name",     "Kanalname",      "str_unicode"),
    "1.3.6.1.4.1.40297.1.2.4.10": ("tx_freq_hz",  "TX-Freq Hz",     "int"),
    "1.3.6.1.4.1.40297.1.2.4.11": ("rx_freq_hz",  "RX-Freq Hz",     "int"),
    "1.3.6.1.4.1.40297.1.2.4.12": ("work_state",  "Arbeitsstatus",  "int"),       # 0=RX,1=TX
    "1.3.6.1.4.1.40297.1.2.4.13": ("zone_alias",  "Zone",           "str_unicode"),
}

# Gruppe 4: rptControl (1.3.6.1.4.1.40297.1.2.2.x)
CONTROL_OIDS = {
    "1.3.6.1.4.1.40297.1.2.2.2":  ("kanal_nr",    "Kanalnummer",    "int"),
    "1.3.6.1.4.1.40297.1.2.2.3":  ("kanal_typ",   "Kanaltyp",       "int"),
    "1.3.6.1.4.1.40297.1.2.2.5":  ("tx_pwr_lvl",  "TX-Power-Level", "int"),       # 0=hoch, 2=niedrig
    "1.3.6.1.4.1.40297.1.2.2.6":  ("knockdown",   "Knockdown",      "int"),
    "1.3.6.1.4.1.40297.1.2.2.7":  ("radio_state", "Radio-State",    "int"),
    "1.3.6.1.4.1.40297.1.2.2.8":  ("trap_ip",     "Trap-IP",        "str"),
    "1.3.6.1.4.1.40297.1.2.2.11": ("rpt_forbid",  "Repeater-Sperre","int"),
}

# Gruppe 5: rptLog (1.3.6.1.4.1.40297.1.2.3.x)
LOG_OIDS = {
    "1.3.6.1.4.1.40297.1.2.3.1.1.2": ("log_alarm_name",   "Log-Alarmtyp",   "int"),
    "1.3.6.1.4.1.40297.1.2.3.1.1.3": ("log_alarm_status", "Log-Alarmstatus","int"),
    "1.3.6.1.4.1.40297.1.2.3.1.1.4": ("log_time",         "Log-Zeit s",     "int"),
    "1.3.6.1.4.1.40297.1.2.3.3":     ("log_count",        "Log-Eintraege",  "int"),
    "1.3.6.1.4.1.40297.1.2.3.4":     ("log_latest",       "Log-Neuester",   "int"),
}

# Alle bekannten OIDs kombiniert
ALL_KNOWN_OIDS: dict = {}
ALL_KNOWN_OIDS.update(ALARM_FLAG_OIDS)
ALL_KNOWN_OIDS.update(PERF_OIDS)
ALL_KNOWN_OIDS.update(SYSINFO_OIDS)
ALL_KNOWN_OIDS.update(CONTROL_OIDS)
ALL_KNOWN_OIDS.update(LOG_OIDS)

# Rueckwaertskompatibilitaet
ALARM_OIDS = {k: (v[0], v[1]) for k, v in ALARM_FLAG_OIDS.items()}
BASE_INFO_OIDS = SYSINFO_OIDS 


@dataclass
class RepeaterAlarmState:
    """Vollstaendiger Zustand des Repeaters aus SNMP-Traps."""
    # ── Alarm-Flags (0=OK, 1=ALARM, None=unbekannt) ──────
    spannung:    Optional[int] = None
    temperatur:  Optional[int] = None
    luefter:     Optional[int] = None
    fw_leistung: Optional[int] = None
    rw_leistung: Optional[int] = None
    vswr_alarm:  Optional[int] = None   # Alarm-Flag
    tx_pll:      Optional[int] = None
    rx_pll:      Optional[int] = None
    batterie:    Optional[int] = None
    # ── Messwerte (Perf) ──────────────────────────────────
    vswr_wert:   Optional[float] = None   # z.B. 1.15
    fw_pwr_watt: Optional[float] = None   # Vorwaertsleistung in Watt
    rw_pwr_watt: Optional[float] = None   # Rueckwaertsleistung in Watt
    temp_wert:   Optional[float] = None   # Temperatur in Grad C
    volt_wert:   Optional[float] = None   # Spannung in Volt
    fan_wert:    Optional[int]   = None   # Luefter RPM
    # ── Geraete-Info (rptSystemInfo) ──────────────────────
    modell_name: str = ""
    modell_nr:   str = ""
    fw_version:  str = ""
    rcdb_version:str = ""
    seriennr:    str = ""
    rpt_alias:   str = ""
    radio_id:    Optional[int]   = None
    ch_type:     Optional[int]   = None   # 0=digital, 1=analog, 2=mixed
    ch_name:     str = ""
    tx_freq_hz:  Optional[int]   = None
    rx_freq_hz:  Optional[int]   = None
    work_state:  Optional[int]   = None   # 0=RX, 1=TX
    zone_alias:  str = ""
    # ── Erweiterte Messwerte ───────────────────────────────
    rssi_slot1:  Optional[int]   = None   # dB
    rssi_slot2:  Optional[int]   = None   # dB
    power_type:  Optional[int]   = None   # 0=DC, 1=Batterie
    batt_connect:Optional[int]   = None   # 0=nein, 1=ja
    batt_volt:   Optional[float] = None   # V
    # ── Meta ──────────────────────────────────────────────
    letzter_trap: float = 0.0
    quelle_ip:   str = ""
    trap_anzahl: int = 0

    @property
    def irgendein_alarm(self) -> bool:
        return any(v == 1 for v in [
            self.vswr_alarm, self.temperatur, self.luefter,
            self.tx_pll, self.rx_pll, self.spannung,
            self.fw_leistung, self.rw_leistung, self.batterie
        ] if v is not None)

    @property
    def alarm_liste(self) -> list:
        mapping = [
            ("vswr_alarm", "VSWR"), ("temperatur", "Temp"),
            ("luefter", "Luefter"), ("tx_pll", "TX-PLL"),
            ("rx_pll", "RX-PLL"), ("spannung", "Spannung"),
            ("fw_leistung", "Fwd-Pwr"), ("rw_leistung", "Rfl-Pwr"),
            ("batterie", "Batt"),
        ]
        return [label for key, label in mapping
                if getattr(self, key, None) == 1]

    def as_dict(self) -> dict:
        return {
            "vswr_alarm": self.vswr_alarm,
            "vswr_wert": self.vswr_wert,
            "fw_pwr_watt": self.fw_pwr_watt,
            "rw_pwr_watt": self.rw_pwr_watt,
            "temp_wert": self.temp_wert,
            "volt_wert": self.volt_wert,
            "fan_wert": self.fan_wert,
            "temperatur_alarm": self.temperatur,
            "luefter_alarm": self.luefter,
            "tx_pll_alarm": self.tx_pll,
            "rx_pll_alarm": self.rx_pll,
            "spannung_alarm": self.spannung,
            "fw_alarm": self.fw_leistung,
            "rw_alarm": self.rw_leistung,
            "batterie_alarm": self.batterie,
            "modell": self.modell,
            "sw_version": self.sw_version,
            "seriennr": self.seriennr,
            "irgendein_alarm": self.irgendein_alarm,
            "quelle_ip": self.quelle_ip,
            "trap_anzahl": self.trap_anzahl,
        }


# ── IEEE 754 Float aus 4-Byte OCTET STRING ───────────────────────────────────
def _octet_to_float(raw: bytes) -> Optional[float]:
    """Konvertiert 4-Byte Big-Endian OCTET STRING zu float (IEEE 754)."""
    if len(raw) == 4:
        try:
            return struct.unpack(">f", raw)[0]
        except Exception:
            pass
    if len(raw) == 8:
        try:
            return struct.unpack(">d", raw)[0]
        except Exception:
            pass
    return None


# ── BER/SNMP Parser ───────────────────────────────────────────────────────────
def _ber_len(data, pos):
    b = data[pos]; pos += 1
    if b < 0x80:
        return b, pos
    n = b & 0x7f
    return int.from_bytes(data[pos:pos+n], 'big'), pos + n

def _ber_oid(data, pos, length):
    end = pos + length
    first = data[pos]
    oid = [first // 40, first % 40]
    pos += 1; val = 0
    while pos < end:
        b = data[pos]; pos += 1
        val = (val << 7) | (b & 0x7f)
        if not (b & 0x80):
            oid.append(val); val = 0
    return ".".join(str(x) for x in oid)

def _ber_int(data, pos, length):
    return int.from_bytes(data[pos:pos+length], 'big', signed=True)

def parse_trap(data: bytes) -> dict:
    """
    Vollstaendiger Trap-Parser.
    Gibt zurueck:
      alarm_updates: {field: 0/1}
      perf_updates:  {field: float/int}
      info_updates:  {field: str}
      unknown_oids:  {oid: raw_hex}
    """
    result = {
        "alarm_updates": {},
        "perf_updates":  {},
        "info_updates":  {},
        "unknown_oids":  {},
        "enterprise": None,
    }
    try:
        pos = 0
        if data[pos] != 0x30: return result
        pos += 1; _, pos = _ber_len(data, pos)
        if data[pos] != 0x02: return result
        pos += 1; vl, pos = _ber_len(data, pos); pos += vl
        if data[pos] != 0x04: return result
        pos += 1; cl, pos = _ber_len(data, pos); pos += cl
        pdu_type = data[pos]; pos += 1; _, pos = _ber_len(data, pos)
        if pdu_type == 0xA4:  # v1 Trap
            if data[pos] == 0x06:
                pos += 1; el, pos = _ber_len(data, pos)
                result["enterprise"] = _ber_oid(data, pos, el); pos += el
            for _ in range(4):
                if pos < len(data):
                    data[pos]; pos += 1; l, pos = _ber_len(data, pos); pos += l
        elif pdu_type in (0xA7, 0xA2, 0xA0, 0xA3):
            for _ in range(3):
                if pos < len(data) and data[pos] == 0x02:
                    pos += 1; l, pos = _ber_len(data, pos); pos += l
        if pos >= len(data) or data[pos] != 0x30: return result
        pos += 1; _, pos = _ber_len(data, pos)
        while pos < len(data):
            if data[pos] != 0x30: break
            pos += 1; _, pos = _ber_len(data, pos)
            if pos >= len(data) or data[pos] != 0x06: break
            pos += 1; ol, pos = _ber_len(data, pos)
            oid_str = _ber_oid(data, pos, ol); pos += ol
            if pos >= len(data): break
            vtype = data[pos]; pos += 1; vl, pos = _ber_len(data, pos)
            raw = data[pos:pos+vl]; pos += vl

            # ── Wert-Typen decodieren ─────────────────────────────────────
            int_val = None
            str_val = None
            if vtype == 0x02:              # INTEGER
                int_val = _ber_int(raw, 0, vl)
            elif vtype == 0x43:           # TimeTicks
                int_val = int.from_bytes(raw, 'big')
            elif vtype == 0x41:           # Counter32
                int_val = int.from_bytes(raw, 'big')
            elif vtype == 0x42:           # Gauge32
                int_val = int.from_bytes(raw, 'big')
            elif vtype == 0x04:           # OCTET STRING
                # Hytera kodiert Strings als UTF-16 LE (Unicode)
                try:
                    s = raw.decode('utf-16-le', errors='replace').rstrip('\x00')
                    str_val = s if s.isprintable() else raw.decode('utf-8', errors='replace').rstrip('\x00')
                except Exception:
                    str_val = raw.decode('utf-8', errors='replace').rstrip('\x00')
            elif vtype == 0x40:           # IpAddress
                if len(raw) == 4:
                    str_val = ".".join(str(b) for b in raw)

            # ── OID zuordnen ─────────────────────────────────────────────────
            matched = False

            # 1. Alarm-Flags
            for alarm_oid, (key, _) in ALARM_FLAG_OIDS.items():
                if oid_str == alarm_oid or oid_str.startswith(alarm_oid + "."):
                    if int_val is not None:
                        result["alarm_updates"][key] = int_val
                    matched = True
                    break

            # 2. Performance-Messwerte (IEEE 754 floats)
            if not matched:
                for perf_oid, (key, label, dtype) in PERF_OIDS.items():
                    if oid_str == perf_oid or oid_str.startswith(perf_oid + "."):
                        if dtype == "float":
                            fval = None
                            if vtype == 0x04:
                                fval = _octet_to_float(raw)
                            elif int_val is not None:
                                fval = float(int_val) / 10.0
                            if fval is not None:
                                result["perf_updates"][key] = round(fval, 3)
                        elif dtype == "int" and int_val is not None:
                            result["perf_updates"][key] = int_val
                        matched = True
                        break

            # 3. System-Info (rptSystemInfo .1.2.4.x) – Unicode strings + ints
            if not matched:
                for info_oid, (key, label, dtype) in SYSINFO_OIDS.items():
                    if oid_str == info_oid or oid_str.startswith(info_oid + "."):
                        if dtype == "int" and int_val is not None:
                            result["info_updates"][key] = int_val
                        elif dtype in ("str", "str_unicode") and str_val is not None:
                            result["info_updates"][key] = str_val.strip()
                        matched = True
                        break

            # 4. Control-OIDs (.1.2.2.x)
            if not matched:
                for ctrl_oid, (key, label, dtype) in CONTROL_OIDS.items():
                    if oid_str == ctrl_oid or oid_str.startswith(ctrl_oid + "."):
                        if dtype == "int" and int_val is not None:
                            result["info_updates"][key] = int_val
                        elif dtype == "str" and str_val is not None:
                            result["info_updates"][key] = str_val
                        matched = True
                        break

            # 5. Log-OIDs (.1.2.3.x)
            if not matched:
                for log_oid, (key, label, dtype) in LOG_OIDS.items():
                    if oid_str == log_oid or oid_str.startswith(log_oid + "."):
                        if int_val is not None:
                            result["info_updates"][key] = int_val
                        matched = True
                        break

            # 6. Wirklich unbekannt
            if not matched:
                result["unknown_oids"][oid_str] = raw.hex()

    except Exception:
        pass
    return result


# ── TrapMonitor-Klasse ────────────────────────────────────────────────────────
class RepeaterTrapMonitor:
    """
    Lauscht auf UDP-Port 10162 auf SNMP-Traps vom HR1065.
    Dekodiert Alarme, Messwerte UND Geraete-Info.
    """

    TRAP_PORT    = 10162
    UNKNOWN_LOG  = os.path.join(os.path.dirname(__file__), "trap_unknown_oids.log")

    def __init__(self,
                 callback: Optional[Callable] = None,
                 log_cb:   Optional[Callable] = None):
        self.callback        = callback
        self.log_cb          = log_cb or (lambda msg, tag="": None)
        self._state          = RepeaterAlarmState()
        self._thread         = None
        self._running        = False
        self._sock           = None
        self._unknown_count  = 0
        self._unknown_oids: Dict[str, str] = {}   # oid -> letzter hex-wert

    @property
    def state(self) -> RepeaterAlarmState:
        return self._state

    def start(self):
        if self._running: return
        self._running = True
        self._thread  = threading.Thread(target=self._listen, daemon=True,
                                         name="RepeaterTrapMonitor")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._sock:
            try: self._sock.close()
            except Exception: pass
        self._sock = None
        # Unbekannte OIDs speichern fuer Analyse
        if self._unknown_oids:
            try:
                with open(self.UNKNOWN_LOG, "w", encoding="utf-8") as f:
                    json.dump(self._unknown_oids, f, indent=2)
            except Exception:
                pass

    def _listen(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.settimeout(1.0)
            self._sock.bind(("0.0.0.0", self.TRAP_PORT))
            self.log_cb(
                f"[Repeater] Trap-Empfaenger aktiv (UDP :{self.TRAP_PORT})", "ok")
        except PermissionError:
            self.log_cb(
                "[Repeater] Port braucht Admin-Rechte – "
                "Dispatcher als Administrator starten!", "err")
            self._running = False
            return
        except OSError as e:
            self.log_cb(f"[Repeater] Trap-Port Fehler: {e}", "err")
            self._running = False
            return

        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break

            parsed       = parse_trap(data)
            alarm_upd    = parsed.get("alarm_updates", {})
            perf_upd     = parsed.get("perf_updates", {})
            info_upd     = parsed.get("info_updates", {})
            unknown_oids = parsed.get("unknown_oids", {})

            self._state.letzter_trap = time.time()
            self._state.quelle_ip    = addr[0]
            self._state.trap_anzahl += 1

            anything = False

            # Alarm-Flags anwenden
            alarm_changed = []
            for key, val in alarm_upd.items():
                old = getattr(self._state, key, None)
                setattr(self._state, key, val)
                if old != val:
                    alarm_changed.append((key, val))
            if alarm_changed:
                anything = True
                for key, val in alarm_changed:
                    label = dict(
                        vswr_alarm="VSWR", temperatur="Temperatur",
                        luefter="Luefter", tx_pll="TX-PLL",
                        rx_pll="RX-PLL", spannung="Spannung",
                        fw_leistung="Fwd-Power", rw_leistung="Rfl-Power",
                        batterie="Batterie"
                    ).get(key, key)
                    tag = "err" if val == 1 else "ok"
                    self.log_cb(
                        f"[Repeater] {label}: {'ALARM!' if val==1 else 'Normal'}"
                        f"  ({addr[0]})", tag)

            # Performance-Werte anwenden
            perf_changed = []
            for key, val in perf_upd.items():
                old = getattr(self._state, key, None)
                setattr(self._state, key, val)
                if old != val:
                    perf_changed.append((key, val))
            if perf_changed:
                anything = True
                parts = []
                for key, val in perf_changed:
                    unit = {"vswr_wert": "", "fw_pwr_watt": " W",
                            "rw_pwr_watt": " W", "temp_wert": " °C",
                            "volt_wert": " V", "fan_wert": " RPM"}.get(key, "")
                    label = {"vswr_wert": "VSWR", "fw_pwr_watt": "FwdPwr",
                             "rw_pwr_watt": "RflPwr", "temp_wert": "Temp",
                             "volt_wert": "Volt", "fan_wert": "Fan"}.get(key, key)
                    parts.append(f"{label}={val}{unit}")
                self.log_cb(f"[Repeater] Messwerte: {', '.join(parts)}", "acc")

            # Geraete-Info anwenden (sysinfo, control, log)
            for key, val in info_upd.items():
                old = getattr(self._state, key, None)
                if old != val and hasattr(self._state, key):
                    setattr(self._state, key, val)
                    anything = True
                    # Wichtige Felder loggen
                    log_keys = {"fw_version": "Firmware", "seriennr": "Seriennr",
                                "rpt_alias": "Alias", "modell_name": "Modell",
                                "tx_freq_hz": "TX-Freq", "rx_freq_hz": "RX-Freq",
                                "ch_name": "Kanal", "radio_id": "Radio-ID"}
                    if key in log_keys:
                        freq_str = f"{val/1e6:.4f} MHz" if "freq" in key and isinstance(val, int) else str(val)
                        self.log_cb(f"[Repeater] {log_keys[key]}: {freq_str}", "acc")

            # Unbekannte OIDs sammeln + erste sofort loggen fuer Diagnose
            if unknown_oids:
                self._unknown_oids.update(unknown_oids)
                self._unknown_count += 1
                if self._unknown_count == 1:
                    self.log_cb(
                        f"[Repeater] Verbindung OK ({addr[0]}) – "
                        f"analysiere {len(unknown_oids)} OIDs...", "ok")
                    # Erste OIDs direkt im Log zeigen (Diagnose)
                    for oid, val_hex in list(unknown_oids.items())[:5]:
                        self.log_cb(f"  OID {oid} = {val_hex[:16]}", "sub")
                elif self._unknown_count == 5:
                    # Nach 5 Traps: Falls immer noch nur unknown -> Diagnose-Hinweis
                    self.log_cb(
                        f"[Repeater] {len(self._unknown_oids)} OIDs noch nicht zugeordnet "
                        f"-> werden in trap_unknown_oids.log gespeichert", "sub")
                elif self._unknown_count % 500 == 0:
                    self.log_cb(
                        f"[Repeater] {self._unknown_count} Traps | "
                        f"{len(self._unknown_oids)} unbekannte OIDs", "sub")

            if self.callback:
                self.callback(self._state)

        self.log_cb("[Repeater] Trap-Empfaenger gestoppt.", "warn")
        # Beim Stoppen: unbekannte OIDs speichern
        if self._unknown_oids:
            try:
                with open(self.UNKNOWN_LOG, "w", encoding="utf-8") as f:
                    json.dump(self._unknown_oids, f, indent=2)
                self.log_cb(
                    f"[Repeater] Unbekannte OIDs gespeichert: {self.UNKNOWN_LOG}",
                    "sub")
            except Exception:
                pass
