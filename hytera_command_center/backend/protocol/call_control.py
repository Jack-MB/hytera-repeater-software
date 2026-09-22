"""
Hytera Command Center – Call Control Parser
Verarbeitet HR1065 NAI RCP Call-Status-Pakete.

Opcodes:
  0x0004  – HR1065 NAI Call-Status (primärer Port CC TS1/TS2)
  0xB845  – Repeater Broadcast TX-Status (Legacy)
  0xB843  – Broadcast TX-Status (Legacy)

Korrekturen gegenüber Vorprojekt:
  Bug B-01: is_emergency = (ct_byte == 0x04) NUR Call-Type, NICHT st_byte
  Bug B-09: RSSI aus 0xB845: signed int16, dann / -2 → dBm
  Bug B-08: Watchdog-Timer (5s ohne CC-Paket → auto PTT_END)
  Bug B-08: Debounce-Timer (0.6s zwischen Start/Ende-Paketen)
"""

import asyncio
import logging
import struct
import time
from typing import Callable, Dict, Any, Optional, Awaitable

logger = logging.getLogger("call_control")

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

# RCP Opcode-Konstanten
OPCODE_NAI_CALL    = 0x0004
OPCODE_LEGACY_B845 = 0xB845
OPCODE_LEGACY_B843 = 0xB843

# RRS-Opcodes (ebenfalls auf CC-Port)
OPCODE_RRS_REG    = 0x0003
OPCODE_RRS_QUERY  = 0x0002
OPCODE_RRS_DEREG  = 0x0001

# Call-Type-Mapping
CALL_TYPES: Dict[int, str] = {
    0x01: "Privat",
    0x02: "Gruppe",
    0x03: "AllCall",
    0x04: "Notruf",
    0x05: "Broadcast",
}

# Watchdog: Wenn kein PTT_END nach diesem Interval kommt → Ende erzwingen
PTT_WATCHDOG_S  = 5.0
# Debounce: Schnelle PTT_START/END-Wechsel ignorieren
PTT_DEBOUNCE_S  = 0.6


class CallControlParser:
    """
    Verarbeitet HR1065 Call-Control-Pakete und emittiert
    ptt_start / ptt_end / emergency / rrs_register / rrs_offline Events.
    """

    def __init__(self, event_callback: EventCallback, slot: str = "TS1"):
        self.event_callback = event_callback
        self.slot           = slot
        self._active_calls: Dict[int, float] = {}   # radio_id → start_time
        self._last_event_ts: Dict[int, float] = {}  # radio_id → last event time
        self._watchdog_tasks: Dict[int, asyncio.Task] = {}

    def _get_current_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    async def _emit(self, event: Dict[str, Any]) -> None:
        try:
            await self.event_callback(event)
        except Exception as exc:
            logger.error(f"CallControl Event-Callback Fehler: {exc}")

    async def _watchdog(self, radio_id: int) -> None:
        """Erzwingt ein PTT_END wenn der Repeater kein Ende-Paket schickt."""
        await asyncio.sleep(PTT_WATCHDOG_S)
        if radio_id in self._active_calls:
            start = self._active_calls.pop(radio_id, time.monotonic())
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.debug(f"CC Watchdog: PTT_END erzwungen für Radio {radio_id}")
            await self._emit({
                "type":        "ptt_end",
                "radio_id":    radio_id,
                "slot":        self.slot,
                "call_type":   "Timeout",
                "is_emergency": False,
                "rssi":        None,
                "duration_ms": max(500, duration_ms),
                "timestamp":   self._get_current_iso(),
                "source":      "watchdog",
            })

    def _cancel_watchdog(self, radio_id: int) -> None:
        task = self._watchdog_tasks.pop(radio_id, None)
        if task and not task.done():
            task.cancel()

    def _restart_watchdog(self, radio_id: int) -> None:
        self._cancel_watchdog(radio_id)
        self._watchdog_tasks[radio_id] = asyncio.create_task(
            self._watchdog(radio_id), name=f"ptt_watchdog_{radio_id}"
        )

    def stop(self) -> None:
        """Stoppt alle aktiven Watchdog-Tasks und leert den Status."""
        for task in self._watchdog_tasks.values():
            if not task.done():
                task.cancel()
        self._watchdog_tasks.clear()
        self._active_calls.clear()

    async def process(self, opcode: int, payload: bytes, addr: tuple) -> None:
        """Verarbeitet ein eingehendes Paket mit dem gegebenen Opcode."""
        if opcode == OPCODE_NAI_CALL:
            await self._handle_nai_call(payload)
        elif opcode == OPCODE_LEGACY_B845:
            await self._handle_b845(payload)
        elif opcode == OPCODE_LEGACY_B843:
            await self._handle_b843(payload)
        elif opcode == OPCODE_RRS_REG:
            await self._handle_rrs(payload, event_type="rrs_register")
        elif opcode == OPCODE_RRS_DEREG:
            await self._handle_rrs(payload, event_type="rrs_offline")
        elif opcode == OPCODE_RRS_QUERY:
            await self._handle_rrs(payload, event_type="rrs_query")
        else:
            logger.debug(f"CC Unbekannter Opcode 0x{opcode:04X} von {addr}, Payload: {payload.hex()[:32]}")

    async def _handle_nai_call(self, payload: bytes) -> None:
        """
        Opcode 0x0004 – HR1065 NAI Call-Status-Paket.
        Mindestlänge 31 Bytes.

        Byte  8: Timeslot  (0x01=TS1, 0x02=TS2)
        Byte  9: Call-Type (0x01=Privat, 0x02=Gruppe, 0x03=AllCall, 0x04=Notruf)
        Byte 16: Status    (0x01–0x04=aktiv, 0x05=Ende, 0x00=Ende)
        Byte 26–29: Sender-ID (uint32 LE)
        Byte 30: Qualitäts-Byte (0–10, für RSSI-Schätzung wenn 0xB845 nicht kommt)
        """
        if len(payload) < 31:
            return

        ts_byte  = payload[8]
        ct_byte  = payload[9]
        st_byte  = payload[16]

        # Sender-ID – Little-Endian uint32
        if len(payload) >= 30:
            sender_id = struct.unpack_from('<I', payload, 26)[0]
        else:
            return

        if sender_id == 0 or sender_id > 0xFFFFFF:
            return

        slot     = "TS2" if ts_byte == 0x02 else "TS1"
        call_type = CALL_TYPES.get(ct_byte, f"Typ 0x{ct_byte:02X}")

        # Bug B-01 Fix: is_emergency NUR wenn Call-Type == 0x04 (Notruf)
        # st_byte 0x04 bedeutet NUR "aktiver Call", KEIN Notruf!
        is_emergency = (ct_byte == 0x04)

        is_active = (1 <= st_byte <= 4 and sender_id > 0)
        is_end    = (st_byte == 5 or st_byte == 0)

        # Qualitäts-RSSI als Fallback (wenn kein 0xB845 folgt)
        # Formel: Qualität 0–10 → ca. -115 bis -55 dBm
        qual_byte = payload[30] if len(payload) > 30 else 5
        rssi_fallback = -115 + (qual_byte * 6) if qual_byte <= 10 else None

        now_mono = time.monotonic()

        # Debounce: Events die zu schnell kommen ignorieren
        last_ts = self._last_event_ts.get(sender_id, 0)
        if now_mono - last_ts < PTT_DEBOUNCE_S and not is_end:
            return
        self._last_event_ts[sender_id] = now_mono

        if is_active and sender_id not in self._active_calls:
            self._active_calls[sender_id] = now_mono
            self._restart_watchdog(sender_id)
            await self._emit({
                "type":        "ptt_start",
                "radio_id":    sender_id,
                "slot":        slot,
                "call_type":   call_type,
                "is_emergency": is_emergency,
                "rssi":        rssi_fallback,
                "timestamp":   self._get_current_iso(),
            })
            if is_emergency:
                await self._emit({
                    "type":      "emergency",
                    "radio_id":  sender_id,
                    "slot":      slot,
                    "call_type": call_type,
                    "timestamp": self._get_current_iso(),
                })

        elif is_end and sender_id in self._active_calls:
            self._cancel_watchdog(sender_id)
            start = self._active_calls.pop(sender_id)
            duration_ms = int((now_mono - start) * 1000)
            await self._emit({
                "type":        "ptt_end",
                "radio_id":    sender_id,
                "slot":        slot,
                "call_type":   call_type,
                "is_emergency": is_emergency,
                "rssi":        rssi_fallback,
                "duration_ms": max(500, duration_ms),
                "timestamp":   self._get_current_iso(),
            })
        elif is_active and sender_id in self._active_calls:
            # Aktives PTT weiterhin aktiv: Watchdog neu starten
            self._restart_watchdog(sender_id)

    async def _handle_b845(self, payload: bytes) -> None:
        """
        Opcode 0xB845 – Repeater Broadcast TX-Status mit echtem RSSI.
        Bug B-09 Fix: RSSI als signed int16, dann / -2 → dBm

        Paket-Format (0xB845):
          Byte  0–3:  Radio-IP (BE uint32) oder Radio-ID
          Byte  4–5:  TX-Status (1=aktiv, 0=Ende)
          Byte  6–7:  RSSI Raw (signed int16 BE → /−2 = dBm)
          Byte  8–11: Call-ID
          Byte 12:    Slot (0x01/0x02)
          Byte 13:    Call-Type
        """
        if len(payload) < 14:
            return

        try:
            radio_ip  = struct.unpack_from('>I', payload, 0)[0]
            radio_id  = radio_ip & 0xFFFFFF
            tx_status = struct.unpack_from('>H', payload, 4)[0]
            rssi_raw  = struct.unpack_from('>h', payload, 6)[0]   # signed int16

            # Bug B-09: Echter RSSI = raw / -2 (dBm, immer negativ)
            rssi_dbm = rssi_raw / -2 if rssi_raw != 0 else None

            slot_byte = payload[12]
            ct_byte   = payload[13] if len(payload) > 13 else 0x02
            slot      = "TS2" if slot_byte == 0x02 else "TS1"
            call_type = CALL_TYPES.get(ct_byte, "Gruppe")

            if radio_id == 0 or radio_id > 0xFFFFFF:
                return

            is_active = (tx_status == 1)
            is_emergency = (ct_byte == 0x04)

            if is_active and radio_id not in self._active_calls:
                self._active_calls[radio_id] = time.monotonic()
                self._restart_watchdog(radio_id)
                await self._emit({
                    "type":        "ptt_start",
                    "radio_id":    radio_id,
                    "slot":        slot,
                    "call_type":   call_type,
                    "is_emergency": is_emergency,
                    "rssi":        rssi_dbm,
                    "timestamp":   self._get_current_iso(),
                })
                if is_emergency:
                    await self._emit({
                        "type":      "emergency",
                        "radio_id":  radio_id,
                        "slot":      slot,
                        "timestamp": self._get_current_iso(),
                    })

            elif not is_active and radio_id in self._active_calls:
                self._cancel_watchdog(radio_id)
                start = self._active_calls.pop(radio_id)
                duration_ms = int((time.monotonic() - start) * 1000)
                await self._emit({
                    "type":        "ptt_end",
                    "radio_id":    radio_id,
                    "slot":        slot,
                    "call_type":   call_type,
                    "is_emergency": is_emergency,
                    "rssi":        rssi_dbm,
                    "duration_ms": max(500, duration_ms),
                    "timestamp":   self._get_current_iso(),
                })

        except struct.error as exc:
            logger.debug(f"CC 0xB845 Parse-Fehler: {exc}")

    async def _handle_b843(self, payload: bytes) -> None:
        """
        Opcode 0xB843 – Älteres Broadcast-TX-Status-Format.
        Ähnlich wie 0xB845 aber mit etwas anderem Layout.
        """
        if len(payload) < 12:
            return
        try:
            radio_ip  = struct.unpack_from('>I', payload, 0)[0]
            radio_id  = radio_ip & 0xFFFFFF
            tx_status = struct.unpack_from('>H', payload, 4)[0]

            # Kein direkter RSSI in 0xB843
            slot_byte = payload[10] if len(payload) > 10 else 0x01
            slot      = "TS2" if slot_byte == 0x02 else "TS1"

            if radio_id == 0 or radio_id > 0xFFFFFF:
                return

            if tx_status == 1 and radio_id not in self._active_calls:
                self._active_calls[radio_id] = time.monotonic()
                self._restart_watchdog(radio_id)
                await self._emit({
                    "type":        "ptt_start",
                    "radio_id":    radio_id,
                    "slot":        slot,
                    "call_type":   "Gruppe",
                    "is_emergency": False,
                    "rssi":        None,
                    "timestamp":   self._get_current_iso(),
                })
            elif tx_status == 0 and radio_id in self._active_calls:
                self._cancel_watchdog(radio_id)
                start = self._active_calls.pop(radio_id)
                duration_ms = int((time.monotonic() - start) * 1000)
                await self._emit({
                    "type":        "ptt_end",
                    "radio_id":    radio_id,
                    "slot":        slot,
                    "call_type":   "Gruppe",
                    "is_emergency": False,
                    "rssi":        None,
                    "duration_ms": max(500, duration_ms),
                    "timestamp":   self._get_current_iso(),
                })
        except struct.error as exc:
            logger.debug(f"CC 0xB843 Parse-Fehler: {exc}")

    async def _handle_rrs(self, payload: bytes, event_type: str) -> None:
        """
        RRS-Pakete (Radio Registration Service).
        Radio-ID Extraktion: LE-Scan, dann BE-Scan, dann Bit-Mask.
        """
        radio_id = _extract_radio_id(payload)
        if radio_id is None:
            return

        # Slot aus Payload versuchen zu lesen (Offset 0 oder 4)
        slot = "TS1"
        if len(payload) >= 2:
            slot_candidate = payload[1]
            if slot_candidate == 0x02:
                slot = "TS2"

        logger.debug(f"RRS {event_type}: Radio {radio_id} ({slot})")
        await self._emit({
            "type":     event_type,
            "radio_id": radio_id,
            "slot":     slot,
            "timestamp": self._get_current_iso(),
        })


def _extract_radio_id(payload: bytes) -> Optional[int]:
    """
    Extrahiert die Radio-ID aus einem RRS/RCP-Payload.
    Versucht verschiedene Byte-Reihenfolgen und Offsets.
    Gültige Range: 1000 – 16776415 (2^24 - 383)
    """
    def valid_id(rid: int) -> bool:
        return 1000 <= rid <= 16776415

    for offset in range(0, min(len(payload) - 3, 8)):
        for fmt in ['>I', '<I']:
            if offset + 4 <= len(payload):
                val = struct.unpack_from(fmt, payload, offset)[0]
                masked = val & 0xFFFFFF
                if valid_id(masked):
                    return masked
                if valid_id(val):
                    return val
    return None
