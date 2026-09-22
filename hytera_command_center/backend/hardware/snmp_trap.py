"""
Hytera Command Center – SNMP Trap Monitor (Hytera HR1065 Repeater)
Empfängt SNMP Traps auf UDP Port 10162 (muss so in CPS konfiguriert werden).

CPS-Einstellung:
  Common → Setting → SNMP Trap Port: 10162
  SNMP Trap IP: <PC-IP-Adresse>

Bug B-10 Fix: Blockierenden Socket-Code aus Coroutine entfernt.
SNMP-Trap-Empfang läuft in run_in_executor (Thread-Pool), damit
der asyncio-Event-Loop nicht blockiert.

Vollständige OID-Map (verifiziert mit LibreNMS HYTERA-REPEATER-MIB):
  1.3.6.1.4.1.40297.1.2.1.1.x – Alarm-Flags
  1.3.6.1.4.1.40297.1.2.1.2.x – Performance-Messwerte
  1.3.6.1.4.1.40297.1.2.4.x   – System-Info
  1.3.6.1.4.1.40297.1.2.2.x   – Control
  1.3.6.1.4.1.40297.1.2.3.x   – Log
"""

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, Callable, Awaitable, List

try:
    from .config import SNMP_TRAP_PORT, REPEATER_IP
except ImportError:
    try:
        from backend.config import SNMP_TRAP_PORT, REPEATER_IP
    except ImportError:
        from config import SNMP_TRAP_PORT, REPEATER_IP

logger = logging.getLogger("snmp_trap")

# ── OID-Map (Hytera HR1065 / HYTERA-REPEATER-MIB & PowerWalker EPPC-MIB) ──────
HYTERA_OID_MAP: Dict[str, Dict[str, str]] = {
    # ── Hytera Alarm-Flags (1.3.6.1.4.1.40297.1.2.1.1.x)
    "1.3.6.1.4.1.40297.1.2.1.1.1":  {"label": "Spannungs-Alarm (Unter-/Überspannung)", "level": "critical"},
    "1.3.6.1.4.1.40297.1.2.1.1.2":  {"label": "Temperatur-Alarm (PA-Überhitzung)",     "level": "critical"},
    "1.3.6.1.4.1.40297.1.2.1.1.3":  {"label": "Lüfter-Störung",                        "level": "critical"},
    "1.3.6.1.4.1.40297.1.2.1.1.4":  {"label": "Vorwärtsleistungs-Alarm (TX Fwd)",       "level": "warning"},
    "1.3.6.1.4.1.40297.1.2.1.1.5":  {"label": "Reflektierte-Leistungs-Alarm (TX Ref)",  "level": "warning"},
    "1.3.6.1.4.1.40297.1.2.1.1.6":  {"label": "VSWR-Alarm (Antenne/Kabel defekt)",     "level": "critical"},
    "1.3.6.1.4.1.40297.1.2.1.1.7":  {"label": "Sender PLL-Alarm (TX PLL Unlock)",      "level": "critical"},
    "1.3.6.1.4.1.40297.1.2.1.1.8":  {"label": "Empfänger PLL-Alarm (RX PLL Unlock)",   "level": "critical"},
    "1.3.6.1.4.1.40297.1.2.1.1.9":  {"label": "Batterie-Tiefentladungs-Alarm",         "level": "critical"},
    "1.3.6.1.4.1.40297.1.2.1.1.10": {"label": "Link-Verbindungsverlust",               "level": "critical"},

    # ── Hytera Performance- & Telemetrie-Werte (1.3.6.1.4.1.40297.1.2.1.2.x)
    "1.3.6.1.4.1.40297.1.2.1.2.1":  {"label": "Eingangsspannung (V)",                 "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.2":  {"label": "PA-Endstufentemperatur (°C)",          "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.3":  {"label": "Lüfterdrehzahl (RPM)",                 "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.4":  {"label": "Stehwellenverhältnis (VSWR)",          "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.5":  {"label": "Vorwärts-Sendeleistung (W)",           "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.6":  {"label": "Reflektierte Sendeleistung (W)",        "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.9":  {"label": "Empfangs-Feldstärke TS1 (dBm)",        "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.10": {"label": "Empfangs-Feldstärke TS2 (dBm)",        "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.11": {"label": "Versorgungsart (0=DC, 1=Batterie)",    "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.12": {"label": "Batterie-Verbindungsstatus",           "level": "info"},
    "1.3.6.1.4.1.40297.1.2.1.2.13": {"label": "Batteriespannung (V)",                 "level": "info"},

    # ── Hytera System-Info (1.3.6.1.4.1.40297.1.2.4.x)
    "1.3.6.1.4.1.40297.1.2.4.1":    {"label": "Seriennummer",                         "level": "info"},
    "1.3.6.1.4.1.40297.1.2.4.2":    {"label": "Firmware-Version",                     "level": "info"},
    "1.3.6.1.4.1.40297.1.2.4.3":    {"label": "Modellbezeichnung",                    "level": "info"},
    "1.3.6.1.4.1.40297.1.2.4.4":    {"label": "Frequenzbereich",                      "level": "info"},
    "1.3.6.1.4.1.40297.1.2.4.5":    {"label": "Betriebszeit (s)",                     "level": "info"},

    # ── PowerWalker / BlueWalker USV Traps (1.3.6.1.4.1.935.10.1.2.x - EPPC-MIB)
    "1.3.6.1.4.1.935.10.1.2.1":     {"label": "USV Stromausfall (Batteriebetrieb)",   "level": "critical"},
    "1.3.6.1.4.1.935.10.1.2.2":     {"label": "USV Batterie fast leer (<20%)",        "level": "critical"},
    "1.3.6.1.4.1.935.10.1.2.3":     {"label": "USV Überlastung",                      "level": "critical"},
    "1.3.6.1.4.1.935.10.1.2.4":     {"label": "USV Übertemperatur",                   "level": "warning"},
    "1.3.6.1.4.1.935.10.1.2.5":     {"label": "USV Bypass aktiv",                     "level": "warning"},
    "1.3.6.1.4.1.935.10.1.2.6":     {"label": "USV Gerätefehler",                     "level": "critical"},

    # ── RFC 1628 Standard UPS-MIB Traps (1.3.6.1.2.1.33.1.6.3.x)
    "1.3.6.1.2.1.33.1.6.3.1":       {"label": "USV Netzausfall (RFC 1628)",          "level": "critical"},
    "1.3.6.1.2.1.33.1.6.3.3":       {"label": "USV Alarmzustand eingetreten",         "level": "warning"},
}

# Standard OID-Felder aus SNMP-v2-PDU
_OID_SYSUPTIME   = "1.3.6.1.2.1.1.3.0"
_OID_TRAP_TYPE   = "1.3.6.1.6.3.1.1.4.1.0"


@dataclass
class SNMPTrapEvent:
    """Decoded SNMP Trap-Ereignis."""
    timestamp:   float     = field(default_factory=time.time)
    source_ip:   str       = ""
    oid_key:     str       = ""
    oid_label:   str       = ""
    value:       str       = ""
    alarm_level: str       = "info"
    raw_bytes:   bytes     = field(default=b"", repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["raw_bytes"] = self.raw_bytes.hex()
        return d


# ── Minimaler SNMP-Trap-Parser (BER-DER ohne externe Bibliothek) ──────────────

def _decode_length(data: bytes, offset: int):
    """BER-Length-Dekodierung. Gibt (length, new_offset) zurück."""
    if offset >= len(data):
        return 0, offset
    first = data[offset]
    offset += 1
    if first & 0x80 == 0:
        return first, offset
    num_bytes = first & 0x7F
    if offset + num_bytes > len(data):
        return 0, offset
    length = int.from_bytes(data[offset:offset + num_bytes], "big")
    return length, offset + num_bytes


def _decode_oid(data: bytes) -> str:
    """BER-OID-Dekodierung zu Punkt-Notation."""
    if len(data) < 1:
        return ""
    result = []
    first = data[0]
    result.append(str(first // 40))
    result.append(str(first % 40))
    i = 1
    current = 0
    while i < len(data):
        byte = data[i]
        current = (current << 7) | (byte & 0x7F)
        if byte & 0x80 == 0:
            result.append(str(current))
            current = 0
        i += 1
    return ".".join(result)


def _parse_snmp_trap_pdu(data: bytes, source_ip: str) -> Optional[SNMPTrapEvent]:
    """
    Parst eine SNMP-v2c-Trap-PDU (BER-kodiert).
    Gibt SNMPTrapEvent oder None bei Parse-Fehler zurück.
    """
    try:
        # SEQUENCE (0x30)
        if not data or data[0] != 0x30:
            return None

        _, offset = _decode_length(data, 1)

        # Version INTEGER
        if data[offset] != 0x02:
            return None
        vlen, offset = _decode_length(data, offset + 1)
        _version = int.from_bytes(data[offset:offset + vlen], "big")
        offset += vlen

        # Community STRING
        if data[offset] != 0x04:
            return None
        clen, offset = _decode_length(data, offset + 1)
        _community = data[offset:offset + clen].decode("latin-1", errors="replace")
        offset += clen

        # PDU-Typ (SNMP-v2-TRAP = 0xA7)
        pdu_type = data[offset]
        if pdu_type not in (0xA4, 0xA7):  # v1 trap oder v2 trap
            return None
        plen, offset = _decode_length(data, offset + 1)

        # Request-ID
        if data[offset] != 0x02:
            return None
        rlen, offset = _decode_length(data, offset + 1)
        offset += rlen

        # Error-Status
        if data[offset] != 0x02:
            return None
        elen, offset = _decode_length(data, offset + 1)
        offset += elen

        # Error-Index
        if data[offset] != 0x02:
            return None
        iilen, offset = _decode_length(data, offset + 1)
        offset += iilen

        # VarBind-Liste (SEQUENCE 0x30)
        if data[offset] != 0x30:
            return None
        _, offset = _decode_length(data, offset + 1)

        # VarBinds parsen – wir suchen die erste Hytera-OID
        first_oid = ""
        first_val = ""
        first_info: Optional[Dict[str, str]] = None

        while offset < len(data):
            if data[offset] != 0x30:
                break
            _, offset = _decode_length(data, offset + 1)

            # OID-Tag (0x06)
            if data[offset] != 0x06:
                break
            oid_len, offset = _decode_length(data, offset + 1)
            oid_raw = data[offset:offset + oid_len]
            oid_str = _decode_oid(oid_raw)
            offset += oid_len

            # Value
            val_tag = data[offset]
            val_len, offset = _decode_length(data, offset + 1)
            val_raw = data[offset:offset + val_len]
            offset += val_len

            val_str = ""
            if val_tag == 0x02:     # INTEGER
                val_str = str(int.from_bytes(val_raw, "big", signed=True))
            elif val_tag == 0x04:   # OCTET STRING
                try:
                    val_str = val_raw.decode("utf-8")
                except UnicodeDecodeError:
                    val_str = val_raw.decode("latin-1", errors="replace")
            elif val_tag == 0x06:   # OID
                val_str = _decode_oid(val_raw)
            elif val_tag in (0x40, 0x41, 0x42, 0x43, 0x44, 0x45):  # Gauge/Counter/IP
                val_str = str(int.from_bytes(val_raw, "big"))
            else:
                val_str = val_raw.hex()

            # Hytera-OID gefunden?
            if oid_str in HYTERA_OID_MAP and not first_info:
                first_oid  = oid_str
                first_val  = val_str
                first_info = HYTERA_OID_MAP[oid_str]

        if not first_oid:
            # Fallback: Allgemeines Trap-Event
            return SNMPTrapEvent(
                source_ip   = source_ip,
                oid_key     = "unknown",
                oid_label   = "SNMP Trap (kein Hytera-OID)",
                value       = "(raw)",
                alarm_level = "info",
                raw_bytes   = data[:64],
            )

        return SNMPTrapEvent(
            source_ip   = source_ip,
            oid_key     = first_oid,
            oid_label   = first_info["label"],
            value       = first_val,
            alarm_level = first_info["level"],
            raw_bytes   = data[:64],
        )

    except Exception as exc:
        logger.debug(f"SNMP-Trap Parse-Fehler: {exc}")
        return None


# ── SNMPTrapMonitor ───────────────────────────────────────────────────────────

class SNMPTrapMonitor:
    """
    Asynchroner UDP-Listener für SNMP-Traps vom Hytera HR1065 Repeater.
    Läuft als Hintergrund-Task und ruft on_trap() bei jedem Trap auf.
    """

    def __init__(
        self,
        port: int = SNMP_TRAP_PORT,
        on_trap: Optional[Callable[[SNMPTrapEvent], Awaitable[None]]] = None,
    ):
        self.port     = port
        self.on_trap  = on_trap
        self.running  = False
        self._sock:   Optional[socket.socket] = None
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        # Status-Tracking
        self._last_trap_ts: Optional[float] = None
        self._trap_count:   int = 0
        self._alarm_count:  int = 0
        self._last_alarm:   Optional[dict] = None
        self._active_alarms: list = []

    def get_state(self) -> dict:
        """Gibt den aktuellen Repeater-Status zurück (basierend auf Trap-History)."""
        online = (
            self._last_trap_ts is not None
            and (time.time() - self._last_trap_ts) < 300  # Online wenn Trap < 5min
        )
        return {
            "online":        online,
            "trap_count":    self._trap_count,
            "alarm_count":   self._alarm_count,
            "last_trap_ts":  self._last_trap_ts,
            "last_alarm":    self._last_alarm,
            "active_alarms": self._active_alarms[-5:],  # max 5 aktive Alarme
        }

    async def start(self) -> None:
        """Startet den SNMP-Trap-Listener-Loop."""
        self.running = True
        self._loop   = asyncio.get_running_loop()

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.port))
            self._sock.setblocking(False)
            logger.info(f"SNMP Trap Listener gestartet (UDP:{self.port})")
        except OSError as exc:
            logger.error(f"SNMP Socket Fehler (Port {self.port}): {exc}")
            self.running = False
            return

        while self.running:
            try:
                data, (src_ip, _) = await self._loop.run_in_executor(
                    None, self._recv_blocking
                )
                if data is None:
                    continue

                event = _parse_snmp_trap_pdu(data, src_ip)
                if event:
                    # State aktualisieren
                    self._last_trap_ts = event.timestamp
                    self._trap_count  += 1
                    if event.alarm_level in ("warning", "critical"):
                        self._alarm_count += 1
                        alarm_dict = event.to_dict()
                        self._last_alarm = alarm_dict
                        self._active_alarms.append(alarm_dict)
                        if len(self._active_alarms) > 20:
                            self._active_alarms = self._active_alarms[-20:]
                    if self.on_trap:
                        await self.on_trap(event)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"SNMP Trap Fehler: {exc}")
                await asyncio.sleep(1)

        if self._sock:
            self._sock.close()
            self._sock = None

    def _recv_blocking(self):
        """Blockierender UDP-Empfang (läuft in Thread-Pool)."""
        if not self._sock or not self.running:
            return None, ("", 0)
        try:
            self._sock.settimeout(2.0)
            return self._sock.recvfrom(4096)
        except socket.timeout:
            return None, ("", 0)
        except Exception as exc:
            logger.debug(f"UDP recv Fehler: {exc}")
            return None, ("", 0)

    def stop(self) -> None:
        """Stoppt den Listener."""
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None