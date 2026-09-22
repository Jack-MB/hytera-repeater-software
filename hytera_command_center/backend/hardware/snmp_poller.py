"""
Hytera Command Center – Aktiver SNMP Poller (Hytera HR1065 / LibreNMS MIB)
Fragt periodisch (alle 30s) Telemetrie- und Leistungswerte direkt vom Repeater
via SNMPv1/SNMPv2c (UDP Port 161) ab:
  - PA-Endstufentemperatur (rptPaTemprature)
  - Stehwellenverhältnis VSWR (rptVswr)
  - Vorwärts- und reflektierte Sendeleistung (rptTxFwdPower, rptTxRefPower)
  - Betriebsspannung (rptVoltage)
  - Empfangsfeldstärke TS1 / TS2 (rptSlot1Rssi, rptSlot2Rssi)
  - Stromversorgung (DC vs. Batterie)
  - Lüfterdrehzahl und Modell-Identifikation

Reine Python-Implementierung (BER/DER) ohne externe C-Bibliotheken.
"""

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, Callable, Awaitable, List, Tuple

try:
    from ..config import (
        REPEATER_IP, SNMP_PORT, SNMP_COMMUNITY, SNMP_POLL_INTERVAL_S,
        RPT_WARN_VSWR_HIGH, RPT_ALARM_VSWR_HIGH,
        RPT_WARN_TEMP_HIGH, RPT_ALARM_TEMP_HIGH,
        RPT_WARN_VOLT_LOW, RPT_WARN_VOLT_HIGH,
    )
except ImportError:
    try:
        from backend.config import (
            REPEATER_IP, SNMP_PORT, SNMP_COMMUNITY, SNMP_POLL_INTERVAL_S,
            RPT_WARN_VSWR_HIGH, RPT_ALARM_VSWR_HIGH,
            RPT_WARN_TEMP_HIGH, RPT_ALARM_TEMP_HIGH,
            RPT_WARN_VOLT_LOW, RPT_WARN_VOLT_HIGH,
        )
    except ImportError:
        REPEATER_IP          = "192.168.0.230"
        SNMP_PORT            = 161
        SNMP_COMMUNITY       = "public"
        SNMP_POLL_INTERVAL_S = 30
        RPT_WARN_VSWR_HIGH   = 2.0
        RPT_ALARM_VSWR_HIGH  = 2.8
        RPT_WARN_TEMP_HIGH   = 60.0
        RPT_ALARM_TEMP_HIGH  = 75.0
        RPT_WARN_VOLT_LOW    = 12.0
        RPT_WARN_VOLT_HIGH   = 15.2

logger = logging.getLogger("snmp_poller")

# ── Hytera MIB OID-Definitionen (HYTERA-REPEATER-MIB) ──────────────────────────
# Basis: 1.3.6.1.4.1.40297.1.2 (hyteraRepeaterMIB.product.repeater)
OID_RPT_VOLTAGE          = "1.3.6.1.4.1.40297.1.2.1.2.1.0"   # OCTET STRING (float)
OID_RPT_PA_TEMPERATURE   = "1.3.6.1.4.1.40297.1.2.1.2.2.0"   # OCTET STRING (float)
OID_RPT_FAN_SPEED        = "1.3.6.1.4.1.40297.1.2.1.2.3.0"   # INTEGER (RPM)
OID_RPT_VSWR             = "1.3.6.1.4.1.40297.1.2.1.2.4.0"   # OCTET STRING (float)
OID_RPT_TX_FWD_POWER     = "1.3.6.1.4.1.40297.1.2.1.2.5.0"   # OCTET STRING (float)
OID_RPT_TX_REF_POWER     = "1.3.6.1.4.1.40297.1.2.1.2.6.0"   # OCTET STRING (float)
OID_RPT_SLOT1_RSSI       = "1.3.6.1.4.1.40297.1.2.1.2.9.0"   # INTEGER (dBm)
OID_RPT_SLOT2_RSSI       = "1.3.6.1.4.1.40297.1.2.1.2.10.0"  # INTEGER (dBm)
OID_RPT_POWER_TYPE       = "1.3.6.1.4.1.40297.1.2.1.2.11.0"  # INTEGER (0=DC, 1=Battery)
OID_RPT_BATTERY_CONNECT  = "1.3.6.1.4.1.40297.1.2.1.2.12.0"  # INTEGER (0=disc, 1=conn)
OID_RPT_SERIAL_NUMBER    = "1.3.6.1.4.1.40297.1.2.4.1.0"     # DisplayString
OID_RPT_FIRMWARE_VER     = "1.3.6.1.4.1.40297.1.2.4.2.0"     # DisplayString
OID_RPT_MODEL_NAME       = "1.3.6.1.4.1.40297.1.2.4.3.0"     # DisplayString

# Gruppe der regelmäßig abgefragten OIDs
TELEMETRY_OIDS: List[str] = [
    OID_RPT_VOLTAGE,
    OID_RPT_PA_TEMPERATURE,
    OID_RPT_FAN_SPEED,
    OID_RPT_VSWR,
    OID_RPT_TX_FWD_POWER,
    OID_RPT_TX_REF_POWER,
    OID_RPT_SLOT1_RSSI,
    OID_RPT_SLOT2_RSSI,
    OID_RPT_POWER_TYPE,
    OID_RPT_BATTERY_CONNECT,
]

SYSTEM_INFO_OIDS: List[str] = [
    OID_RPT_SERIAL_NUMBER,
    OID_RPT_FIRMWARE_VER,
    OID_RPT_MODEL_NAME,
]


# ── BER / SNMPv2c Encoder & Decoder ──────────────────────────────────────────

def _encode_length(length: int) -> bytes:
    """Kodiert eine ASN.1 BER-Länge."""
    if length < 0x80:
        return bytes([length])
    octets = []
    while length > 0:
        octets.append(length & 0xFF)
        length >>= 8
    octets.reverse()
    return bytes([0x80 | len(octets)]) + bytes(octets)


def _encode_oid(oid_str: str) -> bytes:
    """Kodiert eine OID in ASN.1 BER-Format."""
    subids = [int(x) for x in oid_str.strip(".").split(".")]
    if len(subids) < 2:
        return b"\x06\x00"
    encoded = [40 * subids[0] + subids[1]]
    for val in subids[2:]:
        if val == 0:
            encoded.append(0)
        else:
            chunks = []
            while val > 0:
                chunks.append(val & 0x7F)
                val >>= 7
            for i in range(len(chunks) - 1, 0, -1):
                encoded.append(chunks[i] | 0x80)
            encoded.append(chunks[0])
    body = bytes(encoded)
    return b"\x06" + _encode_length(len(body)) + body


def build_snmp_get_packet(community: str, request_id: int, oids: List[str]) -> bytes:
    """
    Erstellt ein standardkonformes SNMPv2c GetRequest-Paket.
    """
    # VarBind-Liste erstellen
    varbinds = b""
    for oid_str in oids:
        # VarBind: SEQUENCE { name ObjectIdentifier, value NULL }
        vb_content = _encode_oid(oid_str) + b"\x05\x00"
        varbinds += b"\x30" + _encode_length(len(vb_content)) + vb_content

    vb_seq = b"\x30" + _encode_length(len(varbinds)) + varbinds

    # GetRequest-PDU (0xA0): [request-id INTEGER, error-status INTEGER 0, error-index INTEGER 0, varbinds]
    req_id_bytes = struct.pack(">I", request_id & 0x7FFFFFFF)
    pdu_body = b"\x02\x04" + req_id_bytes + b"\x02\x01\x00\x02\x01\x00" + vb_seq
    pdu = b"\xA0" + _encode_length(len(pdu_body)) + pdu_body

    # SNMP Message: SEQUENCE { version INTEGER (1=v2c), community OCTET STRING, data PDU }
    comm_bytes = community.encode("latin-1", errors="replace")
    comm_seq = b"\x04" + _encode_length(len(comm_bytes)) + comm_bytes
    msg_body = b"\x02\x01\x01" + comm_seq + pdu
    return b"\x30" + _encode_length(len(msg_body)) + msg_body


def _decode_length(data: bytes, offset: int) -> Tuple[int, int]:
    """Liest Länge und neuen Offset aus ASN.1 BER-Daten."""
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
    """Dekodiert ASN.1 BER-OID zu Punkt-Notation."""
    if len(data) < 1:
        return ""
    result = [str(data[0] // 40), str(data[0] % 40)]
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


def parse_snmp_response_pdu(data: bytes) -> Dict[str, Any]:
    """
    Parst eine SNMP GetResponse-PDU (0xA2).
    Gibt ein Wörterbuch {oid_str: value} zurück.
    """
    result: Dict[str, Any] = {}
    try:
        if not data or data[0] != 0x30:
            return result

        _, offset = _decode_length(data, 1)

        # Version
        if data[offset] != 0x02:
            return result
        vlen, offset = _decode_length(data, offset + 1)
        offset += vlen

        # Community
        if data[offset] != 0x04:
            return result
        clen, offset = _decode_length(data, offset + 1)
        offset += clen

        # PDU-Typ (GetResponse = 0xA2)
        if data[offset] != 0xA2:
            return result
        _, offset = _decode_length(data, offset + 1)

        # Request-ID
        if data[offset] != 0x02:
            return result
        rlen, offset = _decode_length(data, offset + 1)
        offset += rlen

        # Error-Status (0 = noError)
        if data[offset] != 0x02:
            return result
        elen, offset = _decode_length(data, offset + 1)
        err_status = int.from_bytes(data[offset:offset + elen], "big")
        offset += elen
        if err_status != 0:
            logger.debug(f"SNMP Response Error-Status: {err_status}")
            return result

        # Error-Index
        if data[offset] != 0x02:
            return result
        ilen, offset = _decode_length(data, offset + 1)
        offset += ilen

        # VarBind-Liste (SEQUENCE 0x30)
        if offset >= len(data) or data[offset] != 0x30:
            return result
        _, offset = _decode_length(data, offset + 1)

        # Einzelne VarBinds durchlaufen
        while offset < len(data):
            if data[offset] != 0x30:
                break
            _, offset = _decode_length(data, offset + 1)

            # OID Tag (0x06)
            if offset >= len(data) or data[offset] != 0x06:
                break
            oid_len, offset = _decode_length(data, offset + 1)
            oid_str = _decode_oid(data[offset:offset + oid_len])
            offset += oid_len

            if offset >= len(data):
                break

            val_tag = data[offset]
            val_len, offset = _decode_length(data, offset + 1)
            val_bytes = data[offset:offset + val_len]
            offset += val_len

            # Wert typgerecht konvertieren
            val: Any = None
            if val_tag == 0x02:  # INTEGER
                val = int.from_bytes(val_bytes, "big", signed=True)
            elif val_tag == 0x04:  # OCTET STRING (oft IEEE Float oder String)
                if len(val_bytes) == 4:
                    # MIB-Spezifikation: rptVoltage, rptPaTemprature, rptVswr, rptTxFwdPower
                    # sind 4-Byte Floats
                    try:
                        # Zuerst Big-Endian (Standard-Netzwerk-Reihenfolge) versuchen
                        f_val = struct.unpack(">f", val_bytes)[0]
                        # Plausibilitäts-Check: -100 <= x <= 10000
                        if -100.0 <= f_val <= 10000.0:
                            val = round(f_val, 2)
                        else:
                            f_val_le = struct.unpack("<f", val_bytes)[0]
                            val = round(f_val_le, 2) if -100.0 <= f_val_le <= 10000.0 else val_bytes.hex()
                    except Exception:
                        val = val_bytes.decode("latin-1", errors="replace")
                else:
                    try:
                        val = val_bytes.decode("utf-8").strip("\x00").strip()
                    except UnicodeDecodeError:
                        val = val_bytes.decode("latin-1", errors="replace").strip("\x00").strip()
            elif val_tag in (0x40, 0x41, 0x42, 0x43):  # Gauge32 / Counter32 / TimeTicks
                val = int.from_bytes(val_bytes, "big")
            elif val_tag == 0x05:  # NULL
                val = None
            else:
                val = val_bytes.hex()

            result[oid_str] = val

    except Exception as exc:
        logger.debug(f"SNMP Response Parse-Fehler: {exc}")

    return result


# ── HyteraSNMPPoller Klasse ───────────────────────────────────────────────────

class HyteraSNMPPoller:
    """
    Asynchroner Poller für die Live-Hardware-Telemetrie des Hytera HR1065 Repeaters.
    Fragt periodisch alle Telemetrie-OIDs ab und liefert formatierte Messwerte.
    """

    def __init__(
        self,
        repeater_ip: str = REPEATER_IP,
        port: int = SNMP_PORT,
        community: str = SNMP_COMMUNITY,
        interval_s: int = SNMP_POLL_INTERVAL_S,
        on_telemetry: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.repeater_ip    = repeater_ip
        self.port           = port
        self.community      = community
        self.interval_s     = interval_s
        self.on_telemetry   = on_telemetry
        self.running        = False

        self._request_id    = 1000
        self._last_poll_ts: Optional[float] = None
        self._last_success_ts: Optional[float] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None

        # Telemetrie-State (wird für WebSocket & REST vorgehalten)
        self.state: Dict[str, Any] = {
            "online":            False,
            "last_poll_ts":      None,
            "temp_wert":         None,   # °C
            "volt_wert":         None,   # V
            "fw_pwr_watt":       None,   # W
            "ref_pwr_watt":      None,   # W
            "vswr_wert":         None,   # Ratio
            "fan_speed":         None,   # RPM
            "rssi_slot1":        None,   # dBm
            "rssi_slot2":        None,   # dBm
            "power_source":      "DC",   # "DC" | "Batterie"
            "battery_connected": True,
            "serial_number":     "",
            "firmware_version":  "",
            "model_name":        "Hytera HR1065",
            "warnings":          [],
            "poll_count":        0,
            "error_count":       0,
        }

    def get_telemetry_dict(self) -> Dict[str, Any]:
        """Gibt eine Kopie des aktuellen Telemetrie-Status zurück."""
        st = dict(self.state)
        # Online wenn erfolgreicher Poll innerhalb des doppelten Intervalls
        is_online = (
            self._last_success_ts is not None
            and (time.time() - self._last_success_ts) < (self.interval_s * 2.5)
        )
        st["online"] = is_online
        return st

    def _next_request_id(self) -> int:
        self._request_id = (self._request_id + 1) & 0x7FFFFFFF
        return self._request_id

    async def poll_once(self) -> Dict[str, Any]:
        """
        Führt eine einzelne SNMP-Abfragerunde durch (nicht-blockierend via asyncio).
        """
        req_id = self._next_request_id()
        packet = build_snmp_get_packet(self.community, req_id, TELEMETRY_OIDS)

        loop = asyncio.get_running_loop()
        resp_data = await loop.run_in_executor(
            None, self._send_and_receive, packet
        )

        self._last_poll_ts = time.time()
        self.state["last_poll_ts"] = self._last_poll_ts
        self.state["poll_count"] += 1

        if not resp_data:
            self.state["error_count"] += 1
            # Wenn 3 Abfragen nacheinander fehlschlagen → offline
            if self._last_success_ts and (time.time() - self._last_success_ts) > (self.interval_s * 3):
                self.state["online"] = False
            return self.get_telemetry_dict()

        parsed = parse_snmp_response_pdu(resp_data)
        if not parsed:
            return self.get_telemetry_dict()

        self._last_success_ts = time.time()
        self.state["online"] = True

        # Werte zuordnen
        if OID_RPT_VOLTAGE in parsed:
            v = parsed[OID_RPT_VOLTAGE]
            if isinstance(v, (int, float)):
                self.state["volt_wert"] = float(v)

        if OID_RPT_PA_TEMPERATURE in parsed:
            t = parsed[OID_RPT_PA_TEMPERATURE]
            if isinstance(t, (int, float)):
                self.state["temp_wert"] = float(t)

        if OID_RPT_FAN_SPEED in parsed:
            f = parsed[OID_RPT_FAN_SPEED]
            if isinstance(f, int):
                self.state["fan_speed"] = f

        if OID_RPT_VSWR in parsed:
            vswr = parsed[OID_RPT_VSWR]
            if isinstance(vswr, (int, float)):
                self.state["vswr_wert"] = float(vswr)

        if OID_RPT_TX_FWD_POWER in parsed:
            fwd = parsed[OID_RPT_TX_FWD_POWER]
            if isinstance(fwd, (int, float)):
                self.state["fw_pwr_watt"] = float(fwd)

        if OID_RPT_TX_REF_POWER in parsed:
            ref = parsed[OID_RPT_TX_REF_POWER]
            if isinstance(ref, (int, float)):
                self.state["ref_pwr_watt"] = float(ref)

        if OID_RPT_SLOT1_RSSI in parsed:
            rssi1 = parsed[OID_RPT_SLOT1_RSSI]
            if isinstance(rssi1, int):
                self.state["rssi_slot1"] = rssi1

        if OID_RPT_SLOT2_RSSI in parsed:
            rssi2 = parsed[OID_RPT_SLOT2_RSSI]
            if isinstance(rssi2, int):
                self.state["rssi_slot2"] = rssi2

        if OID_RPT_POWER_TYPE in parsed:
            pt = parsed[OID_RPT_POWER_TYPE]
            self.state["power_source"] = "Batterie" if pt == 1 else "DC"

        if OID_RPT_BATTERY_CONNECT in parsed:
            bc = parsed[OID_RPT_BATTERY_CONNECT]
            self.state["battery_connected"] = (bc == 1)

        # Warnungen / Schwellenwertprüfungen generieren
        warnings = []
        vswr_w = self.state.get("vswr_wert")
        if vswr_w is not None:
            if vswr_w >= RPT_ALARM_VSWR_HIGH:
                warnings.append(f"KRITISCH: VSWR {vswr_w:.2f} (Antenne prüfen!)")
            elif vswr_w >= RPT_WARN_VSWR_HIGH:
                warnings.append(f"WARNUNG: VSWR {vswr_w:.2f} leicht erhöht")

        temp_w = self.state.get("temp_wert")
        if temp_w is not None:
            if temp_w >= RPT_ALARM_TEMP_HIGH:
                warnings.append(f"KRITISCH: PA-Temperatur {temp_w:.1f} °C!")
            elif temp_w >= RPT_WARN_TEMP_HIGH:
                warnings.append(f"WARNUNG: PA-Temperatur {temp_w:.1f} °C erhöht")

        volt_w = self.state.get("volt_wert")
        if volt_w is not None:
            if volt_w < RPT_WARN_VOLT_LOW:
                warnings.append(f"WARNUNG: Unterspannung {volt_w:.1f} V")
            elif volt_w > RPT_WARN_VOLT_HIGH:
                warnings.append(f"WARNUNG: Überspannung {volt_w:.1f} V")

        self.state["warnings"] = warnings

        telemetry_dict = self.get_telemetry_dict()
        if self.on_telemetry:
            try:
                await self.on_telemetry(telemetry_dict)
            except Exception as exc:
                logger.error(f"Fehler in on_telemetry Callback: {exc}")

        return telemetry_dict

    def _send_and_receive(self, packet: bytes) -> Optional[bytes]:
        """Sendet ein UDP-Paket synchron mit 2.5s Timeout."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.5)
            sock.sendto(packet, (self.repeater_ip, self.port))
            data, _ = sock.recvfrom(4096)
            return data
        except (socket.timeout, OSError):
            return None
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    async def start(self) -> None:
        """Startet die asynchrone Polling-Schleife."""
        self.running = True
        logger.info(
            f"Hytera SNMP Poller gestartet: {self.repeater_ip}:{self.port} "
            f"(Intervall: {self.interval_s}s)"
        )

        while self.running:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"SNMP Poller Fehler: {exc}")

            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        """Beendet den Poller."""
        self.running = False
