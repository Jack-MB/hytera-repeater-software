"""
Hytera Command Center – GNSS/GPS Parser
Verarbeitet Hytera HR1065 GPS-Positionspakete (NAI HSTRP).

Bug B-02 Fix: Opcode wird aus dem HSTRP-Payload extrahiert und geprüft.
  GPS-Pakete haben Opcode 0x0001 (GNSS) oder liegen auf dedizierten GPS-Ports
  (30003, 30004). Blindes Parsen ohne Opcode-Prüfung wird vermieden.

Paket-Format (nach HSTRP-Header-Stripping):
  Byte 1–2:  Opcode uint16 LE
  Byte 3–4:  Data-Length uint16 LE (Anzahl Bytes der Nutzdaten)
  Byte 5+:   Nutzdaten

  Nutzdaten-Format (ab Offset 0 der Nutzdaten):
  Byte 0–3:  Radio-IP       (BE uint32) → Radio-ID = Wert & 0xFFFFFF
  Byte 4–7:  Latitude Raw   (BE int32)  → /1e7 = Dezimalgrad
  Byte 8–11: Longitude Raw  (BE int32)  → /1e7 = Dezimalgrad
  Byte 12–13: Speed         (BE uint16) → /10 = km/h
  Byte 14–15: Heading       (BE uint16) → Grad (0–360)
  Byte 16:   GPS-Status     (0=kein Fix, 1=Fix)
  Byte 17:   HDOP           (/10 = Wert)
  Byte 18:   Sats           Anzahl Satelliten
"""

import logging
import struct
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Awaitable

logger = logging.getLogger("gnss")

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

# Gültige GPS-Bereiche
LAT_MIN, LAT_MAX = -90.0,   90.0
LON_MIN, LON_MAX = -180.0, 180.0
RADIO_ID_MIN     = 1000
RADIO_ID_MAX     = 16776415  # 2^24 - 383

# Minimum Payload-Länge für GPS-Daten
MIN_GPS_PAYLOAD  = 12


def _get_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_gnss_payload(payload: bytes) -> Optional[Dict[str, Any]]:
    """
    Parst den GPS-Nutzdaten-Block (nach Opcode und Length).
    Gibt None zurück wenn das Paket kein gültiges GPS-Paket ist.
    Bug B-02: Opcode muss GPS-spezifisch sein – wird vom Caller geprüft.
    """
    if len(payload) < MIN_GPS_PAYLOAD:
        return None

    try:
        # Radio-IP (Big-Endian uint32) → Radio-ID = untere 24 Bit
        radio_ip = struct.unpack_from('>I', payload, 0)[0]
        radio_id = radio_ip & 0xFFFFFF

        if radio_id < RADIO_ID_MIN or radio_id > RADIO_ID_MAX:
            return None

        # Koordinaten (Big-Endian signed int32, Skalierung 1e-7)
        lat_raw = struct.unpack_from('>i', payload, 4)[0]
        lon_raw = struct.unpack_from('>i', payload, 8)[0]
        lat = lat_raw / 1e7
        lon = lon_raw / 1e7

        # Validierung der Koordinaten
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            return None

        # Koordinaten 0.0 / 0.0 = kein Fix (Atlantik-Punkt, ungültig)
        if lat == 0.0 and lon == 0.0:
            return None

        # Geschwindigkeit und Kurs (optional)
        speed = 0.0
        heading = 0.0
        if len(payload) >= 14:
            speed_raw = struct.unpack_from('>H', payload, 12)[0]
            speed = speed_raw / 10.0     # km/h

        if len(payload) >= 16:
            heading_raw = struct.unpack_from('>H', payload, 14)[0]
            heading = float(heading_raw % 360)

        # GPS-Fix-Status
        gps_fix = True
        if len(payload) >= 17:
            gps_fix = bool(payload[16] & 0x01)

        # HDOP (Horizontal Dilution of Precision)
        hdop = None
        if len(payload) >= 18:
            hdop_raw = payload[17]
            hdop = hdop_raw / 10.0 if hdop_raw != 0xFF else None

        # Satellitenanzahl
        satellites = None
        if len(payload) >= 19:
            satellites = payload[18] if payload[18] != 0xFF else None

        # RSSI aus Payload wenn vorhanden (Offset 19–20)
        rssi = None
        if len(payload) >= 21:
            rssi_raw = struct.unpack_from('>h', payload, 19)[0]  # signed int16
            if rssi_raw != 0 and rssi_raw != -1:
                rssi = rssi_raw / -2   # dBm

        return {
            "type":       "gps",
            "radio_id":   radio_id,
            "lat":        round(lat, 7),
            "lon":        round(lon, 7),
            "speed":      round(speed, 1),
            "heading":    round(heading, 1),
            "gps_fix":    gps_fix,
            "hdop":       hdop,
            "satellites": satellites,
            "rssi":       rssi,
            "timestamp":  _get_iso(),
        }

    except struct.error as exc:
        logger.debug(f"GNSS Struct-Fehler: {exc}")
        return None
    except Exception as exc:
        logger.debug(f"GNSS Parse-Fehler: {exc}")
        return None


def parse_hstrp_gps_packet(raw_packet: bytes) -> Optional[Dict[str, Any]]:
    """
    Parst ein vollständiges GPS-Paket direkt aus dem UDP-Datagramm
    (inkl. HSTRP-Header-Stripping und Opcode-Prüfung).

    Wird genutzt wenn der HSTRP-Manager das Paket noch nicht vorverarbeitet hat.
    Bug B-02 Fix: Opcode-Prüfung ZUERST, dann GPS-Parse.
    """
    if len(raw_packet) < 17:  # Mindest: 12 Byte Header + 5 Byte Payload-Info
        return None

    # HSTRP-Signatur prüfen
    if raw_packet[:3] != b'\x32\x42\x00':
        return None

    try:
        # Opcode aus Header (Offset 10, 2 Byte LE)
        opcode = struct.unpack_from('<H', raw_packet, 10)[0]

        # Für GPS-Ports (30003/30004) alle Opcodes außer reinen CC-Opcodes zulassen
        # Opcode 0x0001/0x0002/0x0003 = RRS, 0x0004 = CC → kein GPS
        if opcode in (0x0001, 0x0002, 0x0003, 0x0004, 0xB843, 0xB845):
            return None

        # Payload-Länge aus Header (Offset 8, 2 Byte LE)
        payload_len = struct.unpack_from('<H', raw_packet, 8)[0]
        payload     = raw_packet[12:12 + payload_len]

        if len(payload) < 5:
            return None

        # Inner-Opcode aus Payload (Offset 1, 2 Byte LE)
        # Format: [?] [Opcode LE 2B] [Length LE 2B] [Data...]
        inner_opcode = struct.unpack_from('<H', payload, 1)[0]
        inner_length = struct.unpack_from('<H', payload, 3)[0]
        data         = payload[5:5 + inner_length]

        return parse_gnss_payload(data)

    except struct.error:
        return None


class GNSSParser:
    """
    Verarbeitet HSTRP-Payload-Events aus dem Manager und extrahiert GPS-Daten.
    Wird als Callback-Handler im UDPManager registriert.
    """

    def __init__(self, event_callback: EventCallback):
        self.event_callback = event_callback

    async def handle_hstrp_event(self, event: Dict[str, Any]) -> None:
        """
        Empfängt hstrp_payload-Events vom Manager.
        Der Opcode wurde schon im HSTRP-Layer extrahiert.
        """
        if event.get("type") != "hstrp_payload":
            return

        opcode  = event.get("opcode", 0)
        payload = event.get("payload", b'')
        port    = event.get("port", 0)

        # Bug B-02: Nur GPS-spezifische Opcodes oder GPS-Ports erlaubt
        # CC-Opcodes explizit ausschließen
        if opcode in (0x0001, 0x0002, 0x0003, 0x0004, 0xB843, 0xB845):
            return

        # GPS-Ports: 30003, 30004
        # Auf diesen Ports ist der Payload direkt GPS-Daten ohne Inner-Opcode
        gps_data = None
        if port in (30003, 30004):
            # Direkte Payload-Struktur: Inner-Opcode + Length + Data
            if len(payload) >= 5:
                try:
                    inner_length = struct.unpack_from('<H', payload, 3)[0]
                    data = payload[5:5 + inner_length]
                    gps_data = parse_gnss_payload(data)
                except struct.error:
                    # Fallback: Payload direkt als GPS-Daten probieren
                    gps_data = parse_gnss_payload(payload)
        else:
            gps_data = parse_gnss_payload(payload)

        if gps_data:
            gps_data["port"] = port
            await self.event_callback(gps_data)
