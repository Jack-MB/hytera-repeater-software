"""
Hytera Command Center – Vollständiger SNMP Poller (Hytera HR1065 / LibreNMS MIB)
Liest alle verfügbaren Parameter und Leistungsmerkmale des Repeaters aus:
  1. Live RF & Telemetrie:
     - PA-Endstufentemperatur (rptPaTemprature)
     - Stehwellenverhältnis VSWR (rptVswr)
     - Vorwärts- und reflektierte Sendeleistung (rptTxFwdPower, rptTxRefPower)
     - Betriebsspannung & Batteriespannung (rptVoltage, rptBatteryVoltage)
     - Empfangsfeldstärke TS1 & TS2 (rptSlot1Rssi, rptSlot2Rssi)
     - Lüfterdrehzahl (rptFanSpeed)
     - Arbeitszustand Standby / TX / RX (rptWorkState)
     - Stromversorgung: Netzteil DC vs. Notstrom-Batterie (rptSupplyPowerType, rptBatteryConnect)
  2. Kanal- & HF-Parameter:
     - Aktueller Kanalname (rptChannelName) & Kanalnummer (rptChannelNumber)
     - Zonenbezeichnung (rptCurZoneAlias)
     - Kanaltyp (rptCurChannelType: Digital DMR, Analog, Mixed)
     - TX-Sendefrequenz & RX-Empfangsfrequenz in MHz (rptCurTxFreq, rptCurRxFreq)
     - Sendeleistungsstufe High/Low (rptTxPowerLevel)
  3. Geräte-Identifikation & System:
     - Modellname & Modellnummer (rptModelName, rptModelNo)
     - Firmware- & RCDB-Version (rptFirmwareVersion, rptRcdbVersion)
     - Seriennummer (rptSerialNo)
     - Repeater DMR-ID (rptRadioID)
     - Repeater Funkrufname (rptRadioAlias)
     - Betriebszeit / Uptime (sysUpTime)
  4. Aktive Hardware-Schutzalarme:
     - Überspannung/Unterspannung, Übertemperatur, Lüfter, VSWR, PLL-Lock
"""

import asyncio
import logging
import socket
import struct
import time
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

# ── Vollständige Hytera MIB OID-Katalogisierung ────────────────────────────────
# 1. Telemetrie & HF-Messwerte (1.3.6.1.4.1.40297.1.2.1.2.x)
OID_RPT_VOLTAGE          = "1.3.6.1.4.1.40297.1.2.1.2.1.0"   # OCTET STRING (float V)
OID_RPT_PA_TEMPERATURE   = "1.3.6.1.4.1.40297.1.2.1.2.2.0"   # OCTET STRING (float °C)
OID_RPT_FAN_SPEED        = "1.3.6.1.4.1.40297.1.2.1.2.3.0"   # INTEGER (RPM)
OID_RPT_VSWR             = "1.3.6.1.4.1.40297.1.2.1.2.4.0"   # OCTET STRING (float)
OID_RPT_TX_FWD_POWER     = "1.3.6.1.4.1.40297.1.2.1.2.5.0"   # OCTET STRING (float W)
OID_RPT_TX_REF_POWER     = "1.3.6.1.4.1.40297.1.2.1.2.6.0"   # OCTET STRING (float W)
OID_RPT_SLOT1_RSSI       = "1.3.6.1.4.1.40297.1.2.1.2.9.0"   # INTEGER (dBm)
OID_RPT_SLOT2_RSSI       = "1.3.6.1.4.1.40297.1.2.1.2.10.0"  # INTEGER (dBm)
OID_RPT_POWER_TYPE       = "1.3.6.1.4.1.40297.1.2.1.2.11.0"  # INTEGER (0=DC, 1=Battery)
OID_RPT_BATTERY_CONNECT  = "1.3.6.1.4.1.40297.1.2.1.2.12.0"  # INTEGER (0=disc, 1=conn)
OID_RPT_BATTERY_VOLT     = "1.3.6.1.4.1.40297.1.2.1.2.13.0"  # OCTET STRING (float V)

# 2. Kanal- & Betriebsstatus (1.3.6.1.4.1.40297.1.2.4.x & Control .2.x)
OID_RPT_CHANNEL_TYPE     = "1.3.6.1.4.1.40297.1.2.4.8.0"     # INTEGER (0=DMR, 1=Analog, 2=Mixed)
OID_RPT_CHANNEL_NAME     = "1.3.6.1.4.1.40297.1.2.4.9.0"     # OCTET STRING (Name)
OID_RPT_TX_FREQ          = "1.3.6.1.4.1.40297.1.2.4.10.0"    # INTEGER (Hz)
OID_RPT_RX_FREQ          = "1.3.6.1.4.1.40297.1.2.4.11.0"    # INTEGER (Hz)
OID_RPT_WORK_STATE       = "1.3.6.1.4.1.40297.1.2.4.12.0"    # INTEGER (0=RX/Standby, 1=TX)
OID_RPT_ZONE_ALIAS       = "1.3.6.1.4.1.40297.1.2.4.13.0"    # OCTET STRING (Zone)
OID_RPT_CHANNEL_NUM      = "1.3.6.1.4.1.40297.1.2.2.2.0"     # INTEGER (0..15)
OID_RPT_TX_POWER_LVL     = "1.3.6.1.4.1.40297.1.2.2.5.0"     # INTEGER (0=High, 2=Low)
OID_RPT_REPEATING_STATE  = "1.3.6.1.4.1.40297.1.2.2.6.0"     # INTEGER (0=Aktiv, 1=Unterdrückt/Knockdown)
OID_RPT_RADIO_STATUS     = "1.3.6.1.4.1.40297.1.2.2.7.0"     # INTEGER (0=Aktiv, 1=Deaktiviert)

# 3. Geräte-Identifikation (1.3.6.1.4.1.40297.1.2.4.x & MIB-2)
OID_RPT_MODEL_NAME       = "1.3.6.1.4.1.40297.1.2.4.1.0"     # OCTET STRING
OID_RPT_MODEL_NO         = "1.3.6.1.4.1.40297.1.2.4.2.0"     # OCTET STRING
OID_RPT_FIRMWARE_VER     = "1.3.6.1.4.1.40297.1.2.4.3.0"     # OCTET STRING
OID_RPT_FREQ_BAND        = "1.3.6.1.4.1.40297.1.2.4.4.0"     # OCTET STRING
OID_RPT_SERIAL_NO        = "1.3.6.1.4.1.40297.1.2.4.5.0"     # OCTET STRING
OID_RPT_RADIO_ALIAS      = "1.3.6.1.4.1.40297.1.2.4.6.0"     # OCTET STRING
OID_RPT_RADIO_ID         = "1.3.6.1.4.1.40297.1.2.4.7.0"     # INTEGER
OID_RPT_RCDB_VER         = "1.3.6.1.4.1.40297.1.2.4.14.0"    # OCTET STRING
OID_SYS_UPTIME           = "1.3.6.1.2.1.1.3.0"               # TimeTicks

# 4. Repeater-interner Alarm- & Fehlerspeicher (1.3.6.1.4.1.40297.1.2.3.x)
OID_RPT_LOG_COUNT        = "1.3.6.1.4.1.40297.1.2.3.3.0"     # INTEGER (Anzahl Einträge)
OID_RPT_LOG_LATEST       = "1.3.6.1.4.1.40297.1.2.3.4.0"     # INTEGER (Neueste Zeile)
BASE_RPT_LOG_TABLE       = "1.3.6.1.4.1.40297.1.2.3.1.1"

# 5. Hardware-Alarme (1.3.6.1.4.1.40297.1.2.1.1.x)
OID_ALARM_VOLTAGE        = "1.3.6.1.4.1.40297.1.2.1.1.1.0"   # INTEGER (0=norm, 1=low, 2=high)
OID_ALARM_TEMP           = "1.3.6.1.4.1.40297.1.2.1.1.2.0"   # INTEGER (0=norm, 1=low, 2=high)
OID_ALARM_FAN            = "1.3.6.1.4.1.40297.1.2.1.1.3.0"   # INTEGER (0=norm, 1=alarm)
OID_ALARM_FWD_PWR        = "1.3.6.1.4.1.40297.1.2.1.1.4.0"   # INTEGER (0=norm, 1=alarm)
OID_ALARM_REF_PWR        = "1.3.6.1.4.1.40297.1.2.1.1.5.0"   # INTEGER (0=norm, 1=alarm)
OID_ALARM_VSWR           = "1.3.6.1.4.1.40297.1.2.1.1.6.0"   # INTEGER (0=norm, 1=alarm)
OID_ALARM_TX_PLL         = "1.3.6.1.4.1.40297.1.2.1.1.7.0"   # INTEGER (0=norm, 1=alarm)
OID_ALARM_RX_PLL         = "1.3.6.1.4.1.40297.1.2.1.1.8.0"   # INTEGER (0=norm, 1=alarm)
OID_ALARM_BATT_LOW       = "1.3.6.1.4.1.40297.1.2.1.1.9.0"   # INTEGER (0=norm, 1=alarm)
OID_ALARM_LINK_LOSS      = "1.3.6.1.4.1.40297.1.2.1.1.10.0"  # INTEGER (0=norm, 1=alarm)

# 6. Ethernet-Interface Telemetrie (RFC 1213 / IF-MIB auf ifIndex 1)
OID_IF_SPEED             = "1.3.6.1.2.1.2.2.1.5.1"           # Gauge32 (bps)
OID_IF_OPER_STATUS       = "1.3.6.1.2.1.2.2.1.8.1"           # INTEGER (1=up, 2=down)
OID_IF_IN_OCTETS         = "1.3.6.1.2.1.2.2.1.10.1"          # Counter32 (Bytes)
OID_IF_IN_ERRORS         = "1.3.6.1.2.1.2.2.1.14.1"          # Counter32 (Errors)
OID_IF_OUT_OCTETS        = "1.3.6.1.2.1.2.2.1.16.1"          # Counter32 (Bytes)
OID_IF_OUT_ERRORS        = "1.3.6.1.2.1.2.2.1.20.1"          # Counter32 (Errors)

LOG_ALARM_NAMES: Dict[int, str] = {
    0: "PA-Übertemperatur",
    1: "Lüfter-Störung",
    2: "VSWR-Antennenalarm",
    3: "Vorwärtsleistung niedrig",
    4: "Unterspannung",
    5: "Überspannung",
    6: "Sender PLL-Unlock (TX)",
    7: "Empfänger PLL-Unlock (RX)",
    8: "Batterie-Tiefentladung",
    9: "Netzwerk-Linkverlust",
    65535: "(leer)",
}

# Abfrage-Batches zur Vermeidung von UDP-Fragmentierung
BATCH_TELEMETRY = [
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
    OID_RPT_BATTERY_VOLT,
]

BATCH_CHANNEL_AND_ALARMS = [
    OID_RPT_CHANNEL_NAME,
    OID_RPT_ZONE_ALIAS,
    OID_RPT_CHANNEL_NUM,
    OID_RPT_CHANNEL_TYPE,
    OID_RPT_TX_FREQ,
    OID_RPT_RX_FREQ,
    OID_RPT_WORK_STATE,
    OID_RPT_TX_POWER_LVL,
    OID_RPT_REPEATING_STATE,
    OID_RPT_RADIO_STATUS,
    OID_ALARM_VOLTAGE,
    OID_ALARM_TEMP,
    OID_ALARM_FAN,
    OID_ALARM_VSWR,
    OID_ALARM_TX_PLL,
    OID_ALARM_RX_PLL,
]

BATCH_SYSTEM_INFO = [
    OID_RPT_MODEL_NAME,
    OID_RPT_MODEL_NO,
    OID_RPT_FIRMWARE_VER,
    OID_RPT_FREQ_BAND,
    OID_RPT_SERIAL_NO,
    OID_RPT_RADIO_ALIAS,
    OID_RPT_RADIO_ID,
    OID_RPT_LOG_COUNT,
    OID_RPT_LOG_LATEST,
    OID_SYS_UPTIME,
]

BATCH_IF_NETWORK = [
    OID_IF_SPEED,
    OID_IF_OPER_STATUS,
    OID_IF_IN_OCTETS,
    OID_IF_IN_ERRORS,
    OID_IF_OUT_OCTETS,
    OID_IF_OUT_ERRORS,
]


# ── ASN.1 BER Hilfsfunktionen ────────────────────────────────────────────────

def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    octets = []
    while length > 0:
        octets.append(length & 0xFF)
        length >>= 8
    octets.reverse()
    return bytes([0x80 | len(octets)]) + bytes(octets)


def _encode_oid(oid_str: str) -> bytes:
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
    varbinds = b""
    for oid_str in oids:
        vb_content = _encode_oid(oid_str) + b"\x05\x00"
        varbinds += b"\x30" + _encode_length(len(vb_content)) + vb_content

    vb_seq = b"\x30" + _encode_length(len(varbinds)) + varbinds
    req_id_bytes = struct.pack(">I", request_id & 0x7FFFFFFF)
    pdu_body = b"\x02\x04" + req_id_bytes + b"\x02\x01\x00\x02\x01\x00" + vb_seq
    pdu = b"\xA0" + _encode_length(len(pdu_body)) + pdu_body

    comm_bytes = community.encode("latin-1", errors="replace")
    comm_seq = b"\x04" + _encode_length(len(comm_bytes)) + comm_bytes
    msg_body = b"\x02\x01\x01" + comm_seq + pdu
    return b"\x30" + _encode_length(len(msg_body)) + msg_body


def build_snmp_set_int_packet(community: str, request_id: int, oid_str: str, value: int) -> bytes:
    """Erstellt ein SNMP v2c SetRequest-PDU (0xA3) mit einem INTEGER-Wert."""
    val_byte = bytes([value & 0xFF])
    vb_content = _encode_oid(oid_str) + b"\x02\x01" + val_byte
    varbinds = b"\x30" + _encode_length(len(vb_content)) + vb_content
    vb_seq = b"\x30" + _encode_length(len(varbinds)) + varbinds
    req_id_bytes = struct.pack(">I", request_id & 0x7FFFFFFF)
    pdu_body = b"\x02\x04" + req_id_bytes + b"\x02\x01\x00\x02\x01\x00" + vb_seq
    pdu = b"\xA3" + _encode_length(len(pdu_body)) + pdu_body
    comm_bytes = community.encode("latin-1", errors="replace")
    comm_seq = b"\x04" + _encode_length(len(comm_bytes)) + comm_bytes
    msg_body = b"\x02\x01\x01" + comm_seq + pdu
    return b"\x30" + _encode_length(len(msg_body)) + msg_body


def _decode_length(data: bytes, offset: int) -> Tuple[int, int]:
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


def decode_string_value(val_bytes: bytes) -> str:
    """Dekodiert Hytera Unicode (UTF-16LE), UTF-8 oder Latin-1 Strings sauber."""
    if len(val_bytes) >= 2 and val_bytes[1] == 0:
        try:
            s16 = val_bytes.decode("utf-16-le").strip("\x00").strip()
            if s16.isprintable() and len(s16) > 0:
                return s16
        except Exception:
            pass
    try:
        s8 = val_bytes.decode("utf-8").strip("\x00").strip()
        if s8.isprintable() and len(s8) > 0:
            return s8
    except Exception:
        pass
    return val_bytes.decode("latin-1", errors="replace").strip("\x00").strip()


def parse_snmp_response_pdu(data: bytes) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    try:
        if not data or data[0] != 0x30:
            return result

        _, offset = _decode_length(data, 1)
        if data[offset] != 0x02:
            return result
        vlen, offset = _decode_length(data, offset + 1)
        offset += vlen

        if data[offset] != 0x04:
            return result
        clen, offset = _decode_length(data, offset + 1)
        offset += clen

        if data[offset] != 0xA2:  # GetResponse-PDU
            return result
        _, offset = _decode_length(data, offset + 1)

        # Request-ID
        if data[offset] != 0x02:
            return result
        rlen, offset = _decode_length(data, offset + 1)
        offset += rlen

        # Error-Status
        if data[offset] != 0x02:
            return result
        elen, offset = _decode_length(data, offset + 1)
        err_status = int.from_bytes(data[offset:offset + elen], "big")
        offset += elen
        if err_status != 0:
            return result

        # Error-Index
        if data[offset] != 0x02:
            return result
        ilen, offset = _decode_length(data, offset + 1)
        offset += ilen

        # VarBind-Liste
        if offset >= len(data) or data[offset] != 0x30:
            return result
        _, offset = _decode_length(data, offset + 1)

        while offset < len(data):
            if data[offset] != 0x30:
                break
            _, offset = _decode_length(data, offset + 1)

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

            val: Any = None
            if val_tag == 0x02:  # INTEGER
                val = int.from_bytes(val_bytes, "big", signed=True)
            elif val_tag == 0x04:  # OCTET STRING (Float oder String)
                if len(val_bytes) == 4 and any(
                    k in oid_str for k in ("1.2.1.2.1.", "1.2.1.2.2.", "1.2.1.2.4.", "1.2.1.2.5.", "1.2.1.2.6.", "1.2.1.2.13.")
                ):
                    try:
                        f_val = struct.unpack(">f", val_bytes)[0]
                        if -100.0 <= f_val <= 10000.0:
                            val = round(f_val, 2)
                        else:
                            f_le = struct.unpack("<f", val_bytes)[0]
                            val = round(f_le, 2) if -100.0 <= f_le <= 10000.0 else f_val
                    except Exception:
                        val = decode_string_value(val_bytes)
                else:
                    val = decode_string_value(val_bytes)
            elif val_tag in (0x40, 0x41, 0x42, 0x43):  # Gauge / Counter / TimeTicks
                val = int.from_bytes(val_bytes, "big")
            elif val_tag == 0x05:
                val = None
            else:
                val = val_bytes.hex()

            result[oid_str] = val

    except Exception as exc:
        logger.debug(f"SNMP Parse Fehler: {exc}")

    return result


def format_uptime(timeticks: Optional[int]) -> str:
    """Formatiert TimeTicks (1/100 s) in menschenlesbare Tage/Stunden/Minuten."""
    if timeticks is None or timeticks <= 0:
        return "—"
    total_seconds = timeticks // 100
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    return f"{hours:02d}h {minutes:02d}m"


# ── HyteraSNMPPoller Klasse ───────────────────────────────────────────────────

class HyteraSNMPPoller:
    """
    Asynchroner Poller für die vollständige Live-Hardware-Telemetrie
    des Hytera HR1065 Repeaters via SNMPv1/v2c.
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
        self._poll_cycle    = 0

        self._prev_in_octets: Optional[int] = None
        self._prev_out_octets: Optional[int] = None
        self._prev_octets_ts: Optional[float] = None

        # Vollständiger Telemetrie-State
        self.state: Dict[str, Any] = {
            # 1. Verbindungsstatus
            "online":            False,
            "last_poll_ts":      None,
            "poll_count":        0,
            "error_count":       0,

            # 2. HF- & Live-Messwerte
            "temp_wert":         None,   # PA-Temperatur (°C)
            "volt_wert":         None,   # Betriebsspannung (V)
            "batt_volt_wert":    None,   # Akku-Spannung (V)
            "fw_pwr_watt":       None,   # Vorwärtsleistung (W)
            "ref_pwr_watt":      None,   # Reflektierte Leistung (W)
            "vswr_wert":         None,   # Stehwellenverhältnis
            "fan_speed":         None,   # Lüfter (RPM)
            "rssi_slot1":        None,   # Empfangspegel TS1 (dBm)
            "rssi_slot2":        None,   # Empfangspegel TS2 (dBm)
            "power_source":      "DC Netzteil", # "DC Netzteil" | "Notstrom-Batterie"
            "battery_connected": True,

            # 3. Kanal- & Betriebs-Parameter
            "channel_name":      "—",    # z.B. "Kanal 1 TS1/2"
            "zone_alias":        "—",    # z.B. "Einsatz Zone 1"
            "channel_num":       1,
            "channel_type":      "Digital (DMR)", # Digital / Analog / Mixed
            "tx_freq_mhz":       None,   # z.B. 438.8250 MHz
            "rx_freq_mhz":       None,   # z.B. 431.2250 MHz
            "work_state":        0,      # 0=Standby/RX, 1=TX
            "work_state_str":    "Standby",
            "tx_power_level":    "High Power (50W)",

            # 4. Repeater-Sperre & Steuerung (Knockdown)
            "repeating_state":   0,      # 0=Aktiv, 1=Unterdrückt/Knockdown
            "repeating_enabled": True,
            "radio_status":      0,      # 0=Aktiv, 1=Deaktiviert
            "radio_enabled":     True,

            # 5. Geräte-Identifikation & System
            "model_name":        "Hytera HR1065",
            "model_no":          "",
            "freq_band":         "UHF (400-470 MHz)",
            "serial_number":     "—",
            "firmware_version":  "—",
            "rcdb_version":      "—",
            "radio_alias":       "—",
            "radio_id":          None,
            "uptime_str":        "—",
            "uptime_raw":        0,

            # 6. Interner Fehlerspeicher (NVRAM rptLogTable)
            "log_count":         0,
            "log_latest":        0,
            "recent_logs":       [],

            # 7. Ethernet-Interface Telemetrie (IF-MIB)
            "eth_speed_mbps":    100,
            "eth_oper_status":   "Up",
            "eth_in_kbps":       0.0,
            "eth_out_kbps":      0.0,
            "eth_in_errors":     0,
            "eth_out_errors":    0,

            # 8. HF-Rauschflur im Standby
            "noise_floor_dbm":   None,

            # 9. Diagnose & Alarme
            "warnings":          [],
            "active_alarms":     [],
        }

    def get_telemetry_dict(self) -> Dict[str, Any]:
        st = dict(self.state)
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
        """Führt eine mehrstufige SNMP-Abfragerunde für alle Telemetrie- und Identitäts-OIDs durch."""
        loop = asyncio.get_running_loop()
        self._poll_cycle += 1

        # 1. Telemetrie & Leistungswerte abfragen
        p1 = build_snmp_get_packet(self.community, self._next_request_id(), BATCH_TELEMETRY)
        resp1 = await loop.run_in_executor(None, self._send_and_receive, p1)

        # 2. Kanal- und Schutzstatus abfragen
        p2 = build_snmp_get_packet(self.community, self._next_request_id(), BATCH_CHANNEL_AND_ALARMS)
        resp2 = await loop.run_in_executor(None, self._send_and_receive, p2)

        # 3. System-Info (Initial oder alle 5 Zyklen)
        resp3 = None
        if self._poll_cycle == 1 or self._poll_cycle % 5 == 0:
            p3 = build_snmp_get_packet(self.community, self._next_request_id(), BATCH_SYSTEM_INFO)
            resp3 = await loop.run_in_executor(None, self._send_and_receive, p3)

        # 4. Ethernet-Interface Telemetrie (IF-MIB)
        p4 = build_snmp_get_packet(self.community, self._next_request_id(), BATCH_IF_NETWORK)
        resp4 = await loop.run_in_executor(None, self._send_and_receive, p4)

        self._last_poll_ts = time.time()
        self.state["last_poll_ts"] = self._last_poll_ts
        self.state["poll_count"] += 1

        parsed_all: Dict[str, Any] = {}
        if resp1: parsed_all.update(parse_snmp_response_pdu(resp1))
        if resp2: parsed_all.update(parse_snmp_response_pdu(resp2))
        if resp3: parsed_all.update(parse_snmp_response_pdu(resp3))
        if resp4: parsed_all.update(parse_snmp_response_pdu(resp4))

        if not parsed_all:
            self.state["error_count"] += 1
            if self._last_success_ts and (time.time() - self._last_success_ts) > (self.interval_s * 3):
                self.state["online"] = False
            return self.get_telemetry_dict()

        self._last_success_ts = time.time()
        self.state["online"] = True

        # ── 1. Telemetrie parsen ─────────────────────────────────────────────
        if OID_RPT_VOLTAGE in parsed_all and isinstance(parsed_all[OID_RPT_VOLTAGE], (int, float)):
            self.state["volt_wert"] = float(parsed_all[OID_RPT_VOLTAGE])

        if OID_RPT_PA_TEMPERATURE in parsed_all and isinstance(parsed_all[OID_RPT_PA_TEMPERATURE], (int, float)):
            self.state["temp_wert"] = float(parsed_all[OID_RPT_PA_TEMPERATURE])

        if OID_RPT_FAN_SPEED in parsed_all and isinstance(parsed_all[OID_RPT_FAN_SPEED], int):
            self.state["fan_speed"] = parsed_all[OID_RPT_FAN_SPEED]

        if OID_RPT_VSWR in parsed_all and isinstance(parsed_all[OID_RPT_VSWR], (int, float)):
            self.state["vswr_wert"] = float(parsed_all[OID_RPT_VSWR])

        if OID_RPT_TX_FWD_POWER in parsed_all and isinstance(parsed_all[OID_RPT_TX_FWD_POWER], (int, float)):
            self.state["fw_pwr_watt"] = float(parsed_all[OID_RPT_TX_FWD_POWER])

        if OID_RPT_TX_REF_POWER in parsed_all and isinstance(parsed_all[OID_RPT_TX_REF_POWER], (int, float)):
            self.state["ref_pwr_watt"] = float(parsed_all[OID_RPT_TX_REF_POWER])

        if OID_RPT_SLOT1_RSSI in parsed_all and isinstance(parsed_all[OID_RPT_SLOT1_RSSI], int):
            self.state["rssi_slot1"] = parsed_all[OID_RPT_SLOT1_RSSI]

        if OID_RPT_SLOT2_RSSI in parsed_all and isinstance(parsed_all[OID_RPT_SLOT2_RSSI], int):
            self.state["rssi_slot2"] = parsed_all[OID_RPT_SLOT2_RSSI]

        if OID_RPT_POWER_TYPE in parsed_all:
            pt = parsed_all[OID_RPT_POWER_TYPE]
            self.state["power_source"] = "Notstrom-Batterie" if pt == 1 else "DC Netzteil"

        if OID_RPT_BATTERY_CONNECT in parsed_all:
            self.state["battery_connected"] = (parsed_all[OID_RPT_BATTERY_CONNECT] == 1)

        if OID_RPT_BATTERY_VOLT in parsed_all and isinstance(parsed_all[OID_RPT_BATTERY_VOLT], (int, float)):
            self.state["batt_volt_wert"] = float(parsed_all[OID_RPT_BATTERY_VOLT])

        # ── 2. Kanal & HF-Parameter parsen ──────────────────────────────────
        if OID_RPT_CHANNEL_NAME in parsed_all and parsed_all[OID_RPT_CHANNEL_NAME]:
            self.state["channel_name"] = str(parsed_all[OID_RPT_CHANNEL_NAME])

        if OID_RPT_ZONE_ALIAS in parsed_all and parsed_all[OID_RPT_ZONE_ALIAS]:
            self.state["zone_alias"] = str(parsed_all[OID_RPT_ZONE_ALIAS])

        if OID_RPT_CHANNEL_NUM in parsed_all and isinstance(parsed_all[OID_RPT_CHANNEL_NUM], int):
            self.state["channel_num"] = parsed_all[OID_RPT_CHANNEL_NUM] + 1  # 1-indexed

        if OID_RPT_CHANNEL_TYPE in parsed_all:
            ct = parsed_all[OID_RPT_CHANNEL_TYPE]
            type_map = {0: "Digital (DMR)", 1: "Analog (FM)", 2: "Mixed (Auto)"}
            self.state["channel_type"] = type_map.get(ct, "DMR")

        if OID_RPT_TX_FREQ in parsed_all and isinstance(parsed_all[OID_RPT_TX_FREQ], (int, float)):
            hz = float(parsed_all[OID_RPT_TX_FREQ])
            self.state["tx_freq_mhz"] = round(hz / 1_000_000, 4) if hz > 1_000_000 else hz

        if OID_RPT_RX_FREQ in parsed_all and isinstance(parsed_all[OID_RPT_RX_FREQ], (int, float)):
            hz = float(parsed_all[OID_RPT_RX_FREQ])
            self.state["rx_freq_mhz"] = round(hz / 1_000_000, 4) if hz > 1_000_000 else hz

        if OID_RPT_WORK_STATE in parsed_all:
            ws = parsed_all[OID_RPT_WORK_STATE]
            self.state["work_state"] = ws
            self.state["work_state_str"] = "Senden (TX)" if ws == 1 else "Standby / RX"

        if OID_RPT_TX_POWER_LVL in parsed_all:
            pl = parsed_all[OID_RPT_TX_POWER_LVL]
            self.state["tx_power_level"] = "Low Power (25W)" if pl == 2 else "High Power (50W)"

        # Repeater-Sperre & Status
        if OID_RPT_REPEATING_STATE in parsed_all and isinstance(parsed_all[OID_RPT_REPEATING_STATE], int):
            rep_st = parsed_all[OID_RPT_REPEATING_STATE]
            self.state["repeating_state"] = rep_st
            self.state["repeating_enabled"] = (rep_st == 0)

        if OID_RPT_RADIO_STATUS in parsed_all and isinstance(parsed_all[OID_RPT_RADIO_STATUS], int):
            rad_st = parsed_all[OID_RPT_RADIO_STATUS]
            self.state["radio_status"] = rad_st
            self.state["radio_enabled"] = (rad_st == 0)

        # ── 3. Geräte-Identifikation & System ────────────────────────────────
        if OID_RPT_MODEL_NAME in parsed_all and parsed_all[OID_RPT_MODEL_NAME]:
            self.state["model_name"] = str(parsed_all[OID_RPT_MODEL_NAME])

        if OID_RPT_MODEL_NO in parsed_all and parsed_all[OID_RPT_MODEL_NO]:
            self.state["model_no"] = str(parsed_all[OID_RPT_MODEL_NO])

        if OID_RPT_FREQ_BAND in parsed_all and parsed_all[OID_RPT_FREQ_BAND]:
            self.state["freq_band"] = str(parsed_all[OID_RPT_FREQ_BAND])

        if OID_RPT_SERIAL_NO in parsed_all and parsed_all[OID_RPT_SERIAL_NO]:
            self.state["serial_number"] = str(parsed_all[OID_RPT_SERIAL_NO])

        if OID_RPT_FIRMWARE_VER in parsed_all and parsed_all[OID_RPT_FIRMWARE_VER]:
            self.state["firmware_version"] = str(parsed_all[OID_RPT_FIRMWARE_VER])

        if OID_RPT_RCDB_VER in parsed_all and parsed_all[OID_RPT_RCDB_VER]:
            self.state["rcdb_version"] = str(parsed_all[OID_RPT_RCDB_VER])

        if OID_RPT_RADIO_ALIAS in parsed_all and parsed_all[OID_RPT_RADIO_ALIAS]:
            self.state["radio_alias"] = str(parsed_all[OID_RPT_RADIO_ALIAS])

        if OID_RPT_RADIO_ID in parsed_all and isinstance(parsed_all[OID_RPT_RADIO_ID], int):
            self.state["radio_id"] = parsed_all[OID_RPT_RADIO_ID]

        if OID_SYS_UPTIME in parsed_all and isinstance(parsed_all[OID_SYS_UPTIME], int):
            self.state["uptime_raw"] = parsed_all[OID_SYS_UPTIME]
            self.state["uptime_str"] = format_uptime(parsed_all[OID_SYS_UPTIME])

        # ── 4. Interner Fehlerspeicher Zähler ───────────────────────────────
        if OID_RPT_LOG_COUNT in parsed_all and isinstance(parsed_all[OID_RPT_LOG_COUNT], int):
            self.state["log_count"] = parsed_all[OID_RPT_LOG_COUNT]

        if OID_RPT_LOG_LATEST in parsed_all and isinstance(parsed_all[OID_RPT_LOG_LATEST], int):
            self.state["log_latest"] = parsed_all[OID_RPT_LOG_LATEST]

        # ── 5. Ethernet-Interface Telemetrie (IF-MIB) ───────────────────────
        if OID_IF_SPEED in parsed_all and isinstance(parsed_all[OID_IF_SPEED], (int, float)):
            bps = float(parsed_all[OID_IF_SPEED])
            self.state["eth_speed_mbps"] = round(bps / 1_000_000, 1) if bps > 0 else 100

        if OID_IF_OPER_STATUS in parsed_all:
            self.state["eth_oper_status"] = "Up" if parsed_all[OID_IF_OPER_STATUS] == 1 else "Down"

        if OID_IF_IN_ERRORS in parsed_all and isinstance(parsed_all[OID_IF_IN_ERRORS], int):
            self.state["eth_in_errors"] = parsed_all[OID_IF_IN_ERRORS]

        if OID_IF_OUT_ERRORS in parsed_all and isinstance(parsed_all[OID_IF_OUT_ERRORS], int):
            self.state["eth_out_errors"] = parsed_all[OID_IF_OUT_ERRORS]

        now = time.time()
        in_oct = parsed_all.get(OID_IF_IN_OCTETS)
        out_oct = parsed_all.get(OID_IF_OUT_OCTETS)
        if isinstance(in_oct, int) and isinstance(out_oct, int):
            if self._prev_octets_ts and (now - self._prev_octets_ts) > 0:
                dt = now - self._prev_octets_ts
                if self._prev_in_octets is not None and in_oct >= self._prev_in_octets:
                    self.state["eth_in_kbps"] = round(((in_oct - self._prev_in_octets) * 8) / (dt * 1000), 1)
                if self._prev_out_octets is not None and out_oct >= self._prev_out_octets:
                    self.state["eth_out_kbps"] = round(((out_oct - self._prev_out_octets) * 8) / (dt * 1000), 1)
            self._prev_in_octets = in_oct
            self._prev_out_octets = out_oct
            self._prev_octets_ts = now

        # ── 6. Standby Rauschflur-Tracking ──────────────────────────────────
        if self.state["work_state"] == 0:
            rssi = self.state.get("rssi_slot1") or self.state.get("rssi_slot2")
            if rssi is not None and -150 < rssi < 0:
                self.state["noise_floor_dbm"] = rssi

        # ── 7. Alarme & Schwellenwert-Logik ─────────────────────────────────
        warnings = []
        active_alarms = []

        # Prüfe explizite MIB-Alarm-OIDs
        if parsed_all.get(OID_ALARM_VOLTAGE) in (1, 2):
            active_alarms.append({"label": "Spannungs-Alarm (Unter/Überspannung)", "level": "critical"})
        if parsed_all.get(OID_ALARM_TEMP) in (1, 2):
            active_alarms.append({"label": "PA-Überhitzungs-Alarm", "level": "critical"})
        if parsed_all.get(OID_ALARM_FAN) == 1:
            active_alarms.append({"label": "Lüfter-Störung", "level": "critical"})
        if parsed_all.get(OID_ALARM_VSWR) == 1:
            active_alarms.append({"label": "VSWR-Antennenalarm", "level": "critical"})
        if parsed_all.get(OID_ALARM_TX_PLL) == 1:
            active_alarms.append({"label": "Sender PLL-Unlock (TX-Störung)", "level": "critical"})
        if parsed_all.get(OID_ALARM_RX_PLL) == 1:
            active_alarms.append({"label": "Empfänger PLL-Unlock (RX-Störung)", "level": "critical"})

        # Knockdown / Repeating Sperre Warnung
        if not self.state["repeating_enabled"]:
            warnings.append("WARNUNG: Repeater-Betrieb unterdrückt (Knockdown aktiv)")
        if self.state.get("eth_oper_status") == "Down":
            warnings.append("KRITISCH: Repeater Ethernet-Link getrennt (LAN Down)!")
        if (self.state.get("eth_in_errors", 0) + self.state.get("eth_out_errors", 0)) > 10:
            warnings.append("WARNUNG: Ethernet-Paketfehler am Repeater-Port detektiert")

        # Schwellenwerte
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
        self.state["active_alarms"] = active_alarms

        telemetry_dict = self.get_telemetry_dict()
        if self.on_telemetry:
            try:
                await self.on_telemetry(telemetry_dict)
            except Exception as exc:
                logger.error(f"Fehler in on_telemetry Callback: {exc}")

        return telemetry_dict

    async def fetch_recent_logs(self, max_entries: int = 15) -> List[Dict[str, Any]]:
        """Liest die neuesten Einträge aus der internen rptLogTable des Repeaters."""
        log_count = self.state.get("log_count", 0)
        latest = self.state.get("log_latest", 0)
        if log_count <= 0 or latest <= 0:
            return self.state.get("recent_logs", [])

        loop = asyncio.get_running_loop()
        entries = []
        start_idx = latest
        end_idx = max(1, latest - max_entries + 1)
        for i in range(start_idx, end_idx - 1, -1):
            oids = [
                f"{BASE_RPT_LOG_TABLE}.{i}.2.0",
                f"{BASE_RPT_LOG_TABLE}.{i}.3.0",
                f"{BASE_RPT_LOG_TABLE}.{i}.4.0",
            ]
            pkt = build_snmp_get_packet(self.community, self._next_request_id(), oids)
            resp = await loop.run_in_executor(None, self._send_and_receive, pkt)
            if not resp:
                continue
            parsed = parse_snmp_response_pdu(resp)
            code_val = parsed.get(oids[0])
            stat_val = parsed.get(oids[1])
            time_val = parsed.get(oids[2])
            if code_val is None or code_val == 65535:
                continue
            code = int(code_val)
            stat = int(stat_val) if stat_val is not None else 0
            up_s = int(time_val) if time_val is not None else 0
            entries.append({
                "index": i,
                "alarm_code": code,
                "alarm_name": LOG_ALARM_NAMES.get(code, f"Alarm #{code}"),
                "status": "Aktiv" if stat == 1 else "Behoben",
                "is_active": (stat == 1),
                "uptime_s": up_s,
                "time_str": format_uptime(up_s * 100),
            })
        if entries:
            self.state["recent_logs"] = entries
        return self.state["recent_logs"]

    def _send_and_receive(self, packet: bytes) -> Optional[bytes]:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
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

    async def set_repeating_knockdown(self, knockdown: bool) -> bool:
        """Setzt Repeating State via SNMP: 1 = Knockdown (unterdrückt), 0 = Normal/Aktiv."""
        val = 1 if knockdown else 0
        req_id = int(time.time()) & 0x7FFFFFFF
        packet = build_snmp_set_int_packet(self.community, req_id, OID_RPT_REPEATING_STATE, val)
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, self._send_and_receive, packet)
        self.state["repeating_state"] = val
        logger.warning(f"Repeater Knockdown gesetzt: {'AKTIVIERT (Stumm)' if knockdown else 'DEAKTIVIERT (Normal)'}")
        return resp is not None

    async def start(self) -> None:
        self.running = True
        logger.info(
            f"Hytera SNMP Poller (Full Telemetry) gestartet: {self.repeater_ip}:{self.port} "
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
        self.running = False
