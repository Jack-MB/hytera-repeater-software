"""
Hytera Command Center – Radio Registration Service (RRS) Parser
Port 30001 (TS1), Port 30002 (TS2)

Opcodes:
  0x0003 – Radio angemeldet (REGISTER / ONLINE)
  0x0001 – Radio abgemeldet (DEREGISTER / OFFLINE)
  0x0002 – Abfrage / Query (Antwort auf Anfrage)

Radio-ID Extraktion: Hytera codiert die Radio-ID in verschiedenen
Formaten (LE/BE, verschiedene Offsets). Wir scannen systematisch.
"""

import logging
import struct
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Awaitable

logger = logging.getLogger("rrs")

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

OPCODE_RRS_REGISTER   = 0x0003
OPCODE_RRS_DEREGISTER = 0x0001
OPCODE_RRS_QUERY      = 0x0002

RADIO_ID_MIN = 1000
RADIO_ID_MAX = 16776415


def _get_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_radio_id_rrs(payload: bytes) -> Optional[int]:
    """
    Extrahiert die Radio-ID aus einem RRS-Payload.
    Hytera kodiert die ID an verschiedenen Offsets in LE und BE.
    Gültige Range: 1000–16776415.
    """
    def valid(rid: int) -> bool:
        return RADIO_ID_MIN <= rid <= RADIO_ID_MAX

    # Bekannte gute Offsets (basierend auf Protokoll-Analyse)
    offsets = [0, 2, 4, 6, 8]
    for offset in offsets:
        for fmt, size in [('>I', 4), ('<I', 4), ('>H', 2), ('<H', 2)]:
            end = offset + size
            if end > len(payload):
                continue
            try:
                raw = struct.unpack_from(fmt, payload, offset)[0]
                masked = raw & 0xFFFFFF
                if valid(masked):
                    return masked
                if size == 4 and valid(raw):
                    return raw
            except struct.error:
                continue
    return None


def _extract_rrs_slot(payload: bytes) -> str:
    """Versucht den Timeslot aus dem RRS-Payload zu lesen."""
    if len(payload) >= 2:
        candidate = payload[1]
        if candidate == 0x02:
            return "TS2"
    return "TS1"


def parse_rrs_packet(opcode: int, payload: bytes, port: int = 30001) -> Optional[Dict[str, Any]]:
    """
    Parst ein RRS-Paket.
    Gibt None zurück wenn kein gültiges Paket.
    """
    if opcode not in (OPCODE_RRS_REGISTER, OPCODE_RRS_DEREGISTER, OPCODE_RRS_QUERY):
        return None

    radio_id = _extract_radio_id_rrs(payload)
    if radio_id is None:
        logger.debug(f"RRS: Keine gültige Radio-ID in Payload: {payload.hex()}")
        return None

    # Slot aus Port ableiten wenn nicht explizit im Payload
    slot_from_port = "TS2" if port == 30002 else "TS1"
    slot = _extract_rrs_slot(payload) or slot_from_port

    if opcode == OPCODE_RRS_REGISTER:
        event_type = "rrs_register"
        online = True
    elif opcode == OPCODE_RRS_DEREGISTER:
        event_type = "rrs_offline"
        online = False
    else:  # QUERY
        event_type = "rrs_query"
        online = None

    logger.info(f"RRS {event_type}: Radio {radio_id} ({slot}) Port {port}")
    return {
        "type":      event_type,
        "radio_id":  radio_id,
        "online":    online,
        "slot":      slot,
        "port":      port,
        "timestamp": _get_iso(),
    }


class RRSParser:
    """
    Verarbeitet HSTRP-Payload-Events auf RRS-Ports (30001, 30002).
    """

    def __init__(self, event_callback: EventCallback):
        self.event_callback = event_callback

    async def handle_hstrp_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") != "hstrp_payload":
            return

        opcode  = event.get("opcode", 0)
        payload = event.get("payload", b'')
        port    = event.get("port", 30001)

        if opcode not in (OPCODE_RRS_REGISTER, OPCODE_RRS_DEREGISTER, OPCODE_RRS_QUERY):
            return

        result = parse_rrs_packet(opcode, payload, port)
        if result:
            try:
                await self.event_callback(result)
            except Exception as exc:
                logger.error(f"RRS Callback-Fehler: {exc}")
