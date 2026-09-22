"""
Hytera Command Center – SMS/TMP Parser & Sender
Text Message Protocol (TMP) für Hytera DMR

Empfang (Opcodes):
  0x00A1  – TMP Privat MIT ACK-Anforderung
  0x80A1  – TMP Privat OHNE ACK
  0x00B1  – TMP Gruppe

Encoding: UTF-16-LE (BOM optional, wird gestripped)

Paket-Format TMP (nach HSTRP-Header):
  Byte 0–3:  Sender-ID   (BE uint32, untere 24 Bit)
  Byte 4–7:  Target-ID   (BE uint32, untere 24 Bit, 0 bei Gruppe)
  Byte 8–9:  Group-ID    (BE uint16, bei Gruppen-SMS)
  Byte 10:   Encoding    (0x00=ASCII, 0x01=Unicode/UTF-16-LE, 0x04=UTF-16-LE)
  Byte 11:   Msg-Length  (uint8 in Zeichen, NICHT in Bytes)
  Byte 12+:  Text        (UTF-16-LE ohne BOM, 2 Byte/Zeichen)
"""

import asyncio
import logging
import struct
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Awaitable

logger = logging.getLogger("sms")

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

OPCODE_TMP_PRIVATE_ACK    = 0x00A1
OPCODE_TMP_PRIVATE_NO_ACK = 0x80A1
OPCODE_TMP_GROUP          = 0x00B1

RADIO_ID_MIN = 1000
RADIO_ID_MAX = 16776415


def _get_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_tmp_text(payload: bytes, offset: int) -> str:
    """
    Dekodiert den TMP-Textteil.
    Probiert UTF-16-LE, dann UTF-16-BE, dann Latin-1 als Fallback.
    BOM (0xFF 0xFE oder 0xFE 0xFF) wird automatisch gestripped.
    """
    text_bytes = payload[offset:]
    if not text_bytes:
        return ""

    # BOM erkennen und entfernen
    if text_bytes[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text_bytes = text_bytes[2:]

    # UTF-16-LE versuchen (Hytera Standard)
    try:
        return text_bytes.decode('utf-16-le').rstrip('\x00')
    except (UnicodeDecodeError, ValueError):
        pass

    # UTF-16-BE versuchen
    try:
        return text_bytes.decode('utf-16-be').rstrip('\x00')
    except (UnicodeDecodeError, ValueError):
        pass

    # ASCII/Latin-1 Fallback
    try:
        return text_bytes.decode('latin-1').rstrip('\x00')
    except Exception:
        return text_bytes.hex()


def parse_tmp_packet(opcode: int, payload: bytes) -> Optional[Dict[str, Any]]:
    """
    Parst ein TMP-Nachrichten-Paket.
    Gibt None zurück wenn das Paket nicht gültig ist.
    """
    if opcode not in (OPCODE_TMP_PRIVATE_ACK, OPCODE_TMP_PRIVATE_NO_ACK, OPCODE_TMP_GROUP):
        return None

    if len(payload) < 12:
        return None

    try:
        # Sender-ID (BE uint32, untere 24 Bit)
        sender_raw = struct.unpack_from('>I', payload, 0)[0]
        sender_id  = sender_raw & 0xFFFFFF

        # Target-ID (BE uint32, untere 24 Bit) – 0 bei Gruppe
        target_raw = struct.unpack_from('>I', payload, 4)[0]
        target_id  = target_raw & 0xFFFFFF

        # Group-ID (BE uint16)
        group_id = struct.unpack_from('>H', payload, 8)[0]

        # Encoding-Flag
        encoding_flag = payload[10] if len(payload) > 10 else 0x01

        # Nachrichten-Länge in Zeichen
        msg_len_chars = payload[11] if len(payload) > 11 else 0

        # Validierung der IDs
        if sender_id < RADIO_ID_MIN or sender_id > RADIO_ID_MAX:
            return None

        is_group = (opcode == OPCODE_TMP_GROUP)
        needs_ack = (opcode == OPCODE_TMP_PRIVATE_ACK)

        # Text dekodieren
        text = _decode_tmp_text(payload, 12)
        if not text and msg_len_chars == 0:
            text = ""

        return {
            "type":       "sms_received",
            "opcode":     opcode,
            "sender_id":  sender_id,
            "target_id":  target_id,
            "group_id":   group_id if is_group else None,
            "is_group":   is_group,
            "needs_ack":  needs_ack,
            "text":       text,
            "timestamp":  _get_iso(),
        }

    except struct.error as exc:
        logger.debug(f"TMP Parse-Fehler: {exc}")
        return None


def build_tmp_packet(sender_id: int, target_id: int, text: str, is_group: bool = False) -> bytes:
    """
    Baut ein TMP-Nachrichten-Paket zum Senden an ein Radio.
    Verwendet UTF-16-LE Encoding.
    """
    text_bytes = text.encode('utf-16-le')
    msg_len    = len(text)

    opcode = OPCODE_TMP_GROUP if is_group else OPCODE_TMP_PRIVATE_ACK

    # Header
    header = struct.pack('>I', sender_id & 0xFFFFFF)   # Sender-ID
    header += struct.pack('>I', target_id & 0xFFFFFF)  # Target-ID
    header += struct.pack('>H', target_id & 0xFFFF)    # Group-ID (gleich Target bei Gruppe)
    header += bytes([0x01])                             # Encoding: UTF-16-LE
    header += bytes([min(msg_len, 255)])                # Nachrichtenlänge in Zeichen

    return header + text_bytes


class SMSParser:
    """
    Verarbeitet HSTRP-Payload-Events auf TMP-Ports (30007, 30008).
    """

    def __init__(self, event_callback: EventCallback):
        self.event_callback = event_callback

    async def handle_hstrp_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") != "hstrp_payload":
            return

        opcode  = event.get("opcode", 0)
        payload = event.get("payload", b'')

        if opcode not in (OPCODE_TMP_PRIVATE_ACK, OPCODE_TMP_PRIVATE_NO_ACK, OPCODE_TMP_GROUP):
            return

        msg = parse_tmp_packet(opcode, payload)
        if msg:
            logger.info(f"SMS empfangen: Von {msg['sender_id']} → '{msg['text'][:50]}'")
            await self.event_callback(msg)
